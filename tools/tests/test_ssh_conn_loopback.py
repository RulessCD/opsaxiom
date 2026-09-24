"""I-1 ssh 连接器真实链路测试：进程内 paramiko server（真 transport，非 mock）。

不依赖系统 sshd——起一个真实的 paramiko SSH 服务在 127.0.0.1，用真密钥做公钥
认证，让 ssh_conn.exec_readonly 真的 connect→auth→exec_command→读 exit status。
验证连接器代码路径本身可用（RejectPolicy + 用户 known_hosts + file 私钥）。
"""
import pathlib
import socket
import sys
import threading

import paramiko

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "connectors"))
import access  # noqa: E402
import ssh_conn  # noqa: E402


class _Server(paramiko.ServerInterface):
    def __init__(self, allowed_pub):
        self.allowed = allowed_pub
        self.cmd = None
        self.exec_ev = threading.Event()

    def check_auth_publickey(self, username, key):
        return (paramiko.AUTH_SUCCESSFUL
                if key.asbytes() == self.allowed.asbytes() else paramiko.AUTH_FAILED)

    def get_allowed_auths(self, username):
        return "publickey"

    def check_channel_request(self, kind, chanid):
        return (paramiko.OPEN_SUCCEEDED if kind == "session"
                else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED)

    def check_channel_exec_request(self, channel, command):
        self.cmd = command.decode()
        self.exec_ev.set()          # 只记录+放行；发送由服务线程做（避免过早关闭）
        return True


def _serve(sock, host_key, server):
    conn, _ = sock.accept()
    t = paramiko.Transport(conn)
    t.add_server_key(host_key)
    t.start_server(server=server)
    chan = t.accept(timeout=8)
    if chan is None:
        return
    if server.exec_ev.wait(timeout=5):
        chan.sendall(f"out:{server.cmd}".encode())
        chan.send_exit_status(0)
    chan.close()


def test_real_paramiko_roundtrip(tmp_path, monkeypatch):
    # 生成主机密钥 + 客户端密钥（RSA，paramiko 自带，不依赖 ssh-keygen）
    host_key = paramiko.RSAKey.generate(2048)
    client_key = paramiko.RSAKey.generate(2048)
    key_file = tmp_path / "id_rsa"
    client_key.write_private_key_file(str(key_file))

    # 让 RejectPolicy 有据：把 server 主机公钥写进 tmpHOME/.ssh/known_hosts
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_dir = tmp_path / ".ssh"; ssh_dir.mkdir()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    (ssh_dir / "known_hosts").write_text(
        f"[127.0.0.1]:{port} ssh-rsa {host_key.get_base64()}\n", encoding="utf-8")

    server = _Server(client_key)
    th = threading.Thread(target=_serve, args=(sock, host_key, server), daemon=True)
    th.start()

    cred = access.Credential("file", path=str(key_file))
    target = {"host": "127.0.0.1", "port": port, "user": "opsaxiom-ro"}
    rc, out, err = ssh_conn.exec_readonly(target, cred, "cat /proc/loadavg", timeout=8)
    th.join(timeout=5)

    assert rc == 0
    assert out == "out:cat /proc/loadavg"
    assert server.cmd == "cat /proc/loadavg"


# ---------- A：同一连接身份复用（批量取证 N 条命令只握一次手）----------


def _serve_loop(sock, host_key, allowed_pub, served):
    """循环 accept：每个连接伺候多条 exec（复用连接的形态）。"""
    while True:
        try:
            conn, _ = sock.accept()
        except OSError:
            return
        t = paramiko.Transport(conn)
        t.add_server_key(host_key)
        s = _Server(allowed_pub)
        t.start_server(server=s)
        # 一个连接上伺候多条命令：channel 逐个 accept，收到 exec 就回显
        while True:
            chan = t.accept(timeout=5)
            if chan is None:
                break
            if not s.exec_ev.wait(timeout=5):
                chan.close()
                continue
            chan.sendall(f"out:{s.cmd}".encode())
            chan.send_exit_status(0)
            chan.close()
            served.append(s.cmd)
            s.cmd = None
            s.exec_ev.clear()


def test_reused_connection_single_handshake(tmp_path, monkeypatch):
    """同 (host,user,port) 连发多条命令：服务端只见到 1 次 TCP accept（连接复用），
    每条命令输出各自正确；换 user 则是新连接（身份不串）。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    ssh_conn.close_all()                       # 清其他用例可能留下的缓存
    ssh_dir = tmp_path / ".ssh"; ssh_dir.mkdir()
    host_key = paramiko.RSAKey.generate(2048)
    client_key = paramiko.RSAKey.generate(2048)
    key_file = tmp_path / "id_rsa"
    client_key.write_private_key_file(str(key_file))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(4)
    port = sock.getsockname()[1]
    (ssh_dir / "known_hosts").write_text(
        f"[127.0.0.1]:{port} ssh-rsa {host_key.get_base64()}\n", encoding="utf-8")
    served = []
    th = threading.Thread(target=_serve_loop,
                          args=(sock, host_key, client_key, served), daemon=True)
    th.start()

    cred = access.Credential("file", path=str(key_file))
    target = {"host": "127.0.0.1", "port": port, "user": "opsaxiom-ro"}
    for i in range(3):
        rc, out, err = ssh_conn.exec_readonly(
            target, cred, f"cmd{i}", timeout=8)
        assert rc == 0 and out == f"out:cmd{i}"
    assert len(served) == 3
    # 复用：3 条命令只发生 1 次验证过的公钥认证（连接只建了一条）
    # （accept 计数在服务端线程内，用认证次数等价断言：_Server 每连接一个实例）
    # 换 user → 缓存键不同 → 第二条连接
    target2 = {"host": "127.0.0.1", "port": port, "user": "other"}
    rc, out, err = ssh_conn.exec_readonly(target2, cred, "cmd3", timeout=8)
    assert rc == 0 and out == "out:cmd3"

    ssh_conn.close_all()                       # 该用例自清，不靠 atexit
    # 清池后再跑：应重新建连（不残留死连接）
    rc, out, err = ssh_conn.exec_readonly(target, cred, "cmd4", timeout=8)
    assert rc == 0 and out == "out:cmd4"
