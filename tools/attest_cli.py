"""opsaxiom attest 子命令接线（2026-09-11：兑现 docs/10 第三章补交入口）。
薄壳：参数转发给 tools/bin/opsaxiom-attest 的 main，不复制业务逻辑。"""
import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent


def _cmd_attest(args):
    from importlib.machinery import SourceFileLoader
    attest = SourceFileLoader(
        "attest_cli_impl", str(HERE / "bin" / "opsaxiom-attest")).load_module()
    # 主 CLI 解析过的已知参数直接整包转发（opsaxiom-attest.main 接 Namespace）
    return attest.main(args)


def add_attest(subparsers):
    p = subparsers.add_parser("attest", help="实地验证认证：把这次排查沉淀为签名凭据")
    p.add_argument("--skill")
    p.add_argument("--skill-version")
    p.add_argument("--outcome", choices=["resolved", "partial", "failed", "made_worse"])
    p.add_argument("--mode", choices=["navigator", "copilot", "autopilot"])
    p.add_argument("--os-family"); p.add_argument("--os-version")
    p.add_argument("--rollback-exercised", action="store_true")
    p.add_argument("--attestor")
    p.add_argument("--deviation", action="append")
    p.add_argument("--date", help="覆盖日期(测试用，YYYY-MM-DD)")
    p.add_argument("--keygen", action="store_true", help="仅生成/查看签名密钥对后退出")
    p.add_argument("--verify", metavar="FILE", help="验证一个 attestation 文件的签名")
    p.add_argument("--from-session", metavar="SID", help="从会话状态预填 skill/mode/回滚")
    p.set_defaults(fn=_cmd_attest)
