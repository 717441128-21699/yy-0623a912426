import os
import sys
import json
import click
import pickle
import pandas as pd
from datetime import datetime
from colorama import init, Fore, Style

from .config import AppConfig
from .loader import DataLoader, SourceType
from .precheck import PrecheckEngine
from .dedup import DedupEngine
from .exporter import Exporter, BatchComparator
from .models import DedupLevel

init(autoreset=True)

WORKSPACE_DIR = os.path.join(os.path.expanduser("~"), ".clue_cleaner")
SESSION_FILE = os.path.join(WORKSPACE_DIR, "last_session.pkl")
TEMPLATES_DIR = os.path.join(WORKSPACE_DIR, "templates")

os.makedirs(WORKSPACE_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)


def _get_verbose(ctx):
    return getattr(ctx, 'obj_verbose', True)


def _save_session(data: dict):
    data['_saved_at'] = datetime.now().isoformat()
    with open(SESSION_FILE, 'wb') as f:
        pickle.dump(data, f)


def _load_session() -> dict:
    if not os.path.exists(SESSION_FILE):
        return {}
    try:
        with open(SESSION_FILE, 'rb') as f:
            return pickle.load(f)
    except Exception:
        return {}


def _print_banner():
    click.echo(f"""{Fore.CYAN}{Style.BRIGHT}
╔══════════════════════════════════════════╗
║       医美线索清洗工具 v1.0             ║
║   Deduplication for Medical Aesthetics  ║
╚══════════════════════════════════════════╝{Style.RESET_ALL}""")


@click.group(help="🎯 医美线索清洗工具 - 批量判重、挤水、复盘")
@click.option('--config', '-c', 'config_path', type=click.Path(), default=None,
              help='配置文件路径（默认config.yaml）')
@click.option('--quiet/--no-quiet', '-q', default=False, help='静默模式，减少输出')
@click.pass_context
def cli(ctx, config_path, quiet):
    try:
        cfg = AppConfig.load(config_path)
    except FileNotFoundError as e:
        click.echo(f"{Fore.RED}✗ {e}{Style.RESET_ALL}")
        sys.exit(1)
    ctx.obj = cfg
    ctx.obj_verbose = not quiet


# ==================== import 命令 ====================
@cli.command(help="📥 导入并合并多份投放/历史/在跟名单")
@click.option('--campaign', '-m', 'campaign_files', multiple=True, required=True,
              type=click.Path(), help='本次投放文件（可多次指定或通配符）')
@click.option('--history', '-H', 'history_files', multiple=True, type=click.Path(),
              help='历史成交客户文件（可多次）')
@click.option('--following', '-F', 'following_files', multiple=True, type=click.Path(),
              help='门店在跟名单文件（可多次）')
@click.option('--output', '-o', 'output_dir', type=click.Path(), default='output',
              help='合并文件输出目录')
@click.option('--label', '-l', 'channel_label', default=None,
              help='本次投放的渠道标签（如"抖音-618"）')
@click.option('--save-session/--no-save-session', default=True, help='保存本次会话以便后续命令复用')
@click.pass_context
def import_cmd(ctx, campaign_files, history_files, following_files,
               output_dir, channel_label, save_session):
    cfg = ctx.obj
    if _get_verbose(ctx):
        _print_banner()

    loader = DataLoader(cfg, verbose=_get_verbose(ctx))

    click.echo(f"\n{Fore.CYAN}▶ 阶段1: 加载本次投放文件{Style.RESET_ALL}")
    campaign_df, _ = loader.load_multiple(list(campaign_files), SourceType.CAMPAIGN, channel_label)
    campaign_df = loader.normalize_dataframe(campaign_df)

    history_df = None
    if history_files:
        click.echo(f"\n{Fore.CYAN}▶ 阶段2: 加载历史成交文件{Style.RESET_ALL}")
        history_df, _ = loader.load_multiple(list(history_files), SourceType.HISTORY)
        history_df = loader.normalize_dataframe(history_df)

    following_df = None
    if following_files:
        click.echo(f"\n{Fore.CYAN}▶ 阶段3: 加载门店在跟名单{Style.RESET_ALL}")
        following_df, _ = loader.load_multiple(list(following_files), SourceType.FOLLOWING)
        following_df = loader.normalize_dataframe(following_df)

    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime(cfg.output.get('date_format', '%Y%m%d_%H%M%S'))

    imported = {}
    c_path = os.path.join(output_dir, f"_imported_campaign_{ts}.xlsx")
    campaign_df.to_excel(c_path, index=False)
    imported['campaign'] = c_path
    click.echo(f"{Fore.GREEN}✅ 投放数据已缓存: {c_path}{Style.RESET_ALL}")

    if history_df is not None:
        h_path = os.path.join(output_dir, f"_imported_history_{ts}.xlsx")
        history_df.to_excel(h_path, index=False)
        imported['history'] = h_path

    if following_df is not None:
        f_path = os.path.join(output_dir, f"_imported_following_{ts}.xlsx")
        following_df.to_excel(f_path, index=False)
        imported['following'] = f_path

    if save_session:
        _save_session({
            'campaign_df': campaign_df,
            'history_df': history_df,
            'following_df': following_df,
            'imported_paths': imported,
            'output_dir': output_dir,
            'channel_label': channel_label,
        })
        click.echo(f"\n{Fore.GREEN}💾 会话已保存，后续命令可直接使用本次导入的数据{Style.RESET_ALL}")

    click.echo(f"\n{Fore.CYAN}{Style.BRIGHT}📊 导入完成：{Style.RESET_ALL}"
               f"投放 {len(campaign_df)} 条, "
               f"历史 {len(history_df) if history_df is not None else 0} 条, "
               f"在跟 {len(following_df) if following_df is not None else 0} 条")


# ==================== precheck 命令 ====================
@cli.command(help="🔍 预检：字段识别、缺失/异常数据统计")
@click.option('--campaign', '-m', 'campaign_file', type=click.Path(), default=None,
              help='投放文件（留空则使用上次导入的数据）')
@click.option('--errors-out', type=click.Path(), default=None,
              help='导出错误行到指定文件')
@click.pass_context
def precheck(ctx, campaign_file, errors_out):
    cfg = ctx.obj
    if _get_verbose(ctx):
        _print_banner()

    loader = DataLoader(cfg, verbose=_get_verbose(ctx))
    pre = PrecheckEngine(cfg, verbose=_get_verbose(ctx))

    session = _load_session()

    if campaign_file:
        click.echo(f"{Fore.CYAN}▶ 加载预检文件: {campaign_file}{Style.RESET_ALL}")
        df, mappings = loader.load_single(campaign_file, SourceType.CAMPAIGN)
        df = loader.normalize_dataframe(df)
    elif 'campaign_df' in session and session['campaign_df'] is not None:
        click.echo(f"{Fore.CYAN}▶ 使用会话中已导入的投放数据{Style.RESET_ALL}")
        df = session['campaign_df']
        mappings = []
    else:
        click.echo(f"{Fore.RED}✗ 请先使用 import 命令导入数据，或通过 -m 指定文件{Style.RESET_ALL}")
        sys.exit(1)

    result = pre.run(df, mappings)
    pre.print_summary(result)

    if errors_out:
        os.makedirs(os.path.dirname(errors_out) or '.', exist_ok=True)
        bad_idx = set(result.missing_phone_rows) | set(result.invalid_phone_rows) | set(result.invalid_wechat_rows)
        if bad_idx:
            err_df = df.loc[sorted(bad_idx)].copy()
            err_df.insert(0, '_错误类型', '')
            for idx in result.missing_phone_rows:
                if idx in err_df.index:
                    err_df.at[idx, '_错误类型'] = str(err_df.at[idx, '_错误类型'] or '') + '缺失手机号;'
            for idx in result.invalid_phone_rows:
                if idx in err_df.index:
                    err_df.at[idx, '_错误类型'] = str(err_df.at[idx, '_错误类型'] or '') + '手机号格式异常;'
            for idx in result.invalid_wechat_rows:
                if idx in err_df.index:
                    err_df.at[idx, '_错误类型'] = str(err_df.at[idx, '_错误类型'] or '') + '微信号格式异常;'
            err_df.to_excel(errors_out, index=False)
            click.echo(f"{Fore.GREEN}✅ 错误行已导出: {errors_out}{Style.RESET_ALL}")

    session['precheck_result'] = result
    _save_session(session)


# ==================== dedup 命令 ====================
@cli.command(help="🎯 判重：按手机号/微信号/姓名生日等多规则匹配")
@click.option('--campaign', '-m', 'campaign_file', type=click.Path(), default=None,
              help='本次投放文件')
@click.option('--history', '-H', 'history_file', type=click.Path(), default=None,
              help='历史成交客户文件')
@click.option('--following', '-F', 'following_file', type=click.Path(), default=None,
              help='门店在跟名单')
@click.option('--rules', '-r', 'rule_template', type=str, default=None,
              help='规则模板名称（saved_templates中保存的）')
@click.option('--output', '-o', 'output_dir', type=click.Path(), default='output',
              help='输出目录')
@click.option('--skip-export', is_flag=True, help='仅判重不导出')
@click.pass_context
def dedup(ctx, campaign_file, history_file, following_file,
          rule_template, output_dir, skip_export):
    cfg = ctx.obj
    if _get_verbose(ctx):
        _print_banner()

    loader = DataLoader(cfg, verbose=_get_verbose(ctx))
    session = _load_session()

    if rule_template:
        tpl_path = os.path.join(TEMPLATES_DIR, f"{rule_template}.yaml")
        if os.path.exists(tpl_path):
            cfg = AppConfig.load(tpl_path)
            click.echo(f"{Fore.GREEN}▶ 已加载规则模板: {rule_template}{Style.RESET_ALL}")
        else:
            click.echo(f"{Fore.YELLOW}⚠ 模板不存在，使用默认配置: {tpl_path}{Style.RESET_ALL}")

    def _get_df(key, file_opt, src_type, label=None):
        if file_opt:
            df, _ = loader.load_single(file_opt, src_type, label)
            return loader.normalize_dataframe(df)
        return session.get(key)

    campaign_df = _get_df('campaign_df', campaign_file, SourceType.CAMPAIGN)
    history_df = _get_df('history_df', history_file, SourceType.HISTORY)
    following_df = _get_df('following_df', following_file, SourceType.FOLLOWING)

    if campaign_df is None or campaign_df.empty:
        click.echo(f"{Fore.RED}✗ 没有投放数据，请先用 import 或 -m 指定{Style.RESET_ALL}")
        sys.exit(1)

    engine = DedupEngine(cfg, verbose=_get_verbose(ctx))
    result = engine.run(campaign_df, history_df, following_df)

    session['dedup_result'] = result
    session['output_dir'] = output_dir
    _save_session(session)

    if not skip_export:
        exporter = Exporter(cfg, output_dir=output_dir, verbose=_get_verbose(ctx))
        paths = exporter.export_all(result)
        exporter.generate_review_package(result['records'], campaign_df)

        session['export_paths'] = paths
        _save_session(session)


# ==================== review 命令 ====================
@cli.command(help="📋 生成人工复核表（含疑似重复对比）")
@click.option('--output', '-o', 'output_dir', type=click.Path(), default=None,
              help='输出目录')
@click.option('--filename', '-f', default=None, help='自定义文件名')
@click.pass_context
def review(ctx, output_dir, filename):
    cfg = ctx.obj
    if _get_verbose(ctx):
        _print_banner()

    session = _load_session()
    result = session.get('dedup_result')

    if result is None:
        click.echo(f"{Fore.RED}✗ 请先执行 dedup 命令进行判重{Style.RESET_ALL}")
        sys.exit(1)

    out_dir = output_dir or session.get('output_dir', 'output')
    exporter = Exporter(cfg, output_dir=out_dir, verbose=_get_verbose(ctx))
    fn = filename or "人工复核包.xlsx"
    path = exporter.generate_review_package(result['records'],
                                            result.get('campaign_df'),
                                            filename=fn)
    if path:
        click.echo(f"\n{Fore.GREEN}{Style.BRIGHT}📋 复核包生成完成！{Style.RESET_ALL}")
        click.echo(f"  路径: {path}")
        click.echo(f"  使用说明:")
        click.echo(f"   1. 打开【待复核】sheet")
        click.echo(f"   2. 对比每组A/B两行的联系方式、姓名、意向")
        click.echo(f"   3. 在【人工复核结果】列填写结论（参考复核参考sheet）")
        click.echo(f"   4. 保存后通过 export --apply-review 导入复核结论")


# ==================== export 命令 ====================
@cli.command(help="💾 导出结果：有效线索、日志、统计、复核结果应用")
@click.option('--output', '-o', 'output_dir', type=click.Path(), default=None,
              help='输出目录')
@click.option('--format', '-f', 'fmt', type=click.Choice(['all', 'valid', 'log', 'summary', 'json']),
              default='all', help='导出内容: all=全部, valid=有效线索, log=重复日志, summary=汇总, json=结构化')
@click.option('--apply-review', type=click.Path(), default=None,
              help='导入已填好的人工复核表，覆盖判重结论')
@click.option('--save-template', 'tpl_name', type=str, default=None,
              help='将当前配置保存为规则模板（指定模板名）')
@click.pass_context
def export_cmd(ctx, output_dir, fmt, apply_review, tpl_name):
    cfg = ctx.obj
    if _get_verbose(ctx):
        _print_banner()

    session = _load_session()

    if tpl_name:
        safe = "".join(c for c in tpl_name if c.isalnum() or c in '-_')
        tpl_path = os.path.join(TEMPLATES_DIR, f"{safe}.yaml")
        cfg.save(tpl_path)
        click.echo(f"{Fore.GREEN}✅ 规则模板已保存: {tpl_path}{Style.RESET_ALL}")

    result = session.get('dedup_result')
    if result is None and fmt != 'json':
        click.echo(f"{Fore.RED}✗ 请先执行 dedup 命令{Style.RESET_ALL}")
        sys.exit(1)

    if apply_review and os.path.exists(apply_review):
        click.echo(f"{Fore.CYAN}▶ 应用人工复核结果: {apply_review}{Style.RESET_ALL}")
        result = _apply_review_result(cfg, result, apply_review)
        session['dedup_result'] = result
        _save_session(session)

    out_dir = output_dir or session.get('output_dir', 'output')
    exporter = Exporter(cfg, output_dir=out_dir, verbose=_get_verbose(ctx))

    if fmt in ('all', 'valid') and result:
        exporter.export_valid_clues(result['records'])
    if fmt in ('all', 'log') and result:
        exporter.export_duplicate_log(result['records'])
    if fmt in ('all', 'summary') and result:
        exporter.export_summary(result['summary'])
    if fmt in ('all',) and result:
        exporter.export_full_results(result['records'])
    if fmt == 'json':
        summary = result['summary'] if result else {}
        os.makedirs(out_dir, exist_ok=True)
        json_path = os.path.join(out_dir, f"result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        click.echo(f"{Fore.GREEN}✅ JSON导出: {json_path}{Style.RESET_ALL}")


def _apply_review_result(cfg, result: dict, review_path: str) -> dict:
    records = result['records']
    try:
        df = pd.read_excel(review_path, sheet_name='待复核', dtype=str)
    except Exception as e:
        click.echo(f"{Fore.RED}✗ 读取复核表失败: {e}{Style.RESET_ALL}")
        return result

    applied = 0
    for _, row in df.iterrows():
        decision = str(row.get('人工复核结果', '')).strip()
        if not decision:
            continue

        def _safe_int(x):
            try:
                return int(float(x))
            except Exception:
                return -1

        a = _safe_int(row.get('A行号', -1))
        b = _safe_int(row.get('B行号', -1))

        if 'B剔除' in decision and b in records:
            records[b].keep_row = False
            records[b].level = DedupLevel.DEFINITE
            applied += 1
        if 'A剔除' in decision and a in records:
            records[a].keep_row = False
            records[a].level = DedupLevel.DEFINITE
            applied += 1
        if '都保留' in decision:
            if a in records:
                records[a].keep_row = True
                records[a].level = DedupLevel.UNIQUE
            if b in records:
                records[b].keep_row = True
                records[b].level = DedupLevel.UNIQUE
            applied += 1
        if '都剔除' in decision:
            if a in records:
                records[a].keep_row = False
                records[a].level = DedupLevel.DEFINITE
            if b in records:
                records[b].keep_row = False
                records[b].level = DedupLevel.DEFINITE
            applied += 1

    click.echo(f"{Fore.GREEN}▶ 已应用 {applied} 条人工复核结论{Style.RESET_ALL}")
    result['records'] = records

    engine = DedupEngine(cfg, verbose=False)
    result['summary'] = engine._generate_summary(records)
    return result


# ==================== compare 命令 ====================
@cli.command(help="⚖ 批次对比：两个判重会话或两个文件的差异分析")
@click.option('--a-file', type=click.Path(), default=None, help='A批文件（默认使用会话结果）')
@click.option('--b-file', type=click.Path(), required=True, help='B批投放文件')
@click.option('--history', '-H', type=click.Path(), default=None, help='共用的历史成交库')
@click.option('--following', '-F', type=click.Path(), default=None, help='共用的在跟库')
@click.option('--label-a', default='批次A', help='A批标签')
@click.option('--label-b', default='批次B', help='B批标签')
@click.option('--output', '-o', 'output_dir', type=click.Path(), default='output',
              help='输出目录')
@click.pass_context
def compare(ctx, a_file, b_file, history, following,
            label_a, label_b, output_dir):
    cfg = ctx.obj
    if _get_verbose(ctx):
        _print_banner()

    loader = DataLoader(cfg, verbose=_get_verbose(ctx))
    dedup_engine = DedupEngine(cfg, verbose=_get_verbose(ctx))
    session = _load_session()

    def _run_dedup_for(campaign_file, desc):
        click.echo(f"\n{Fore.CYAN}▶ 判重 {desc}...{Style.RESET_ALL}")
        c_df, _ = loader.load_single(campaign_file, SourceType.CAMPAIGN)
        c_df = loader.normalize_dataframe(c_df)
        h_df = None
        f_df = None
        if history:
            h_df, _ = loader.load_single(history, SourceType.HISTORY)
            h_df = loader.normalize_dataframe(h_df)
        if following:
            f_df, _ = loader.load_single(following, SourceType.FOLLOWING)
            f_df = loader.normalize_dataframe(f_df)
        return dedup_engine.run(c_df, h_df, f_df)

    if a_file:
        result_a = _run_dedup_for(a_file, label_a)
    elif 'dedup_result' in session and session['dedup_result']:
        click.echo(f"{Fore.CYAN}▶ 使用会话中已有的 {label_a} 结果{Style.RESET_ALL}")
        result_a = session['dedup_result']
    else:
        click.echo(f"{Fore.RED}✗ 需要 --a-file 指定A批，或先执行 dedup{Style.RESET_ALL}")
        sys.exit(1)

    result_b = _run_dedup_for(b_file, label_b)

    comparator = BatchComparator(cfg, verbose=_get_verbose(ctx))
    cmp_result = comparator.compare(result_a, result_b, label_a, label_b)
    comparator.export_comparison(cmp_result, output_dir, label_a, label_b)


def main():
    cli()


if __name__ == '__main__':
    main()
