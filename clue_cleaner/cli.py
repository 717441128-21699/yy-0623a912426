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


def _parse_date(s: str):
    s = str(s).strip()
    fmts = [
        '%Y-%m-%d', '%Y/%m/%d', '%Y%m%d',
        '%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S',
        '%Y年%m月%d日', '%m/%d/%Y'
    ]
    for fmt in fmts:
        try:
            return pd.to_datetime(s, format=fmt)
        except Exception:
            continue
    try:
        return pd.to_datetime(s, errors='coerce')
    except Exception:
        return pd.NaT


def _apply_filters(df: pd.DataFrame,
                   date_from: str = None, date_to: str = None,
                   channel: str = None, campaign_tag: str = None,
                   source_file: str = None) -> pd.DataFrame:
    if df is None:
        return df
    mask = pd.Series(True, index=df.index)

    date_cols = [c for c in df.columns if c in ('date', '录入日期', '投放日期', '日期',
                                                '成交日期', '到店日期')
                 or 'date' in str(c).lower() or '日期' in str(c)]
    dt_series = None
    for c in date_cols:
        if c in df.columns:
            dt_series = pd.to_datetime(df[c], errors='coerce')
            break

    if dt_series is not None and dt_series.notna().any():
        if date_from:
            dfrom = _parse_date(date_from)
            if pd.notna(dfrom):
                mask &= (dt_series >= dfrom) | dt_series.isna()
        if date_to:
            dto = _parse_date(date_to) + pd.Timedelta(days=1)
            if pd.notna(dto):
                mask &= (dt_series < dto) | dt_series.isna()

    if channel:
        chs = [x.strip() for x in channel.split(',') if x.strip()]
        if chs:
            col = 'channel' if 'channel' in df.columns else None
            for c in df.columns:
                if '渠道' in str(c):
                    col = c
                    break
            if col:
                ch_mask = pd.Series(False, index=df.index)
                for ch in chs:
                    ch_mask |= df[col].astype(str).str.contains(ch, case=False, na=False)
                mask &= ch_mask

    if campaign_tag:
        tags = [x.strip() for x in campaign_tag.split(',') if x.strip()]
        if tags:
            cand_cols = [c for c in df.columns
                         if any(k in str(c) for k in
                                ['活动', '批次', 'campaign', '计划', '标签', 'batch', 'tag'])
                         or c in ('campaign',)]
            tag_mask = pd.Series(False, index=df.index)
            for col in (cand_cols or df.columns.tolist()):
                col_series = df[col].astype(str)
                for t in tags:
                    tag_mask |= col_series.str.contains(t, case=False, na=False)
            mask &= tag_mask

    if source_file:
        files = [x.strip() for x in source_file.split(',') if x.strip()]
        if files and '_source_file' in df.columns:
            sf_mask = pd.Series(False, index=df.index)
            for fn in files:
                sf_mask |= df['_source_file'].astype(str).str.contains(fn, case=False, na=False)
            mask &= sf_mask

    return df.loc[mask].copy()


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
@click.option('--report', is_flag=True, help='同时生成运营周报')
@click.option('--date-from', 'date_from', type=str, default=None,
              help='筛选起始日期（格式 2024-06-01 或 20240601）')
@click.option('--date-to', 'date_to', type=str, default=None,
              help='筛选截止日期（格式 2024-06-30 或 20240630）')
@click.option('--channel', 'filter_channel', type=str, default=None,
              help='按渠道筛选（如 抖音，支持逗号分隔多渠道）')
@click.option('--campaign-tag', 'campaign_tag', type=str, default=None,
              help='按活动名/批次标签筛选，支持逗号分隔，支持模糊匹配')
@click.option('--source-file', 'source_file', type=str, default=None,
              help='按源文件名筛选，支持模糊匹配，逗号分隔')
@click.pass_context
def dedup(ctx, campaign_file, history_file, following_file,
          rule_template, output_dir, skip_export, report,
          date_from, date_to, filter_channel, campaign_tag, source_file):
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

    filter_info = {}
    if date_from: filter_info['起始日期'] = date_from
    if date_to: filter_info['截止日期'] = date_to
    if filter_channel: filter_info['渠道'] = filter_channel
    if campaign_tag: filter_info['活动/批次'] = campaign_tag
    if source_file: filter_info['源文件'] = source_file

    if filter_info:
        campaign_df = _apply_filters(campaign_df, date_from, date_to,
                                     filter_channel, campaign_tag, source_file)
        if campaign_df is None or campaign_df.empty:
            click.echo(f"{Fore.RED}✗ 筛选后无数据，请放宽条件{Style.RESET_ALL}")
            sys.exit(1)
        click.echo(f"{Fore.YELLOW}🔍 筛选后剩余 {len(campaign_df)} 条投放线索{Style.RESET_ALL}")

    engine = DedupEngine(cfg, verbose=_get_verbose(ctx))
    result = engine.run(campaign_df, history_df, following_df, filters=filter_info)

    session['dedup_result'] = result
    session['output_dir'] = output_dir
    _save_session(session)

    if not skip_export:
        exporter = Exporter(cfg, output_dir=output_dir, verbose=_get_verbose(ctx))
        paths = exporter.export_all(result)
        exporter.generate_review_package(result['records'], campaign_df)

        session['export_paths'] = paths
        _save_session(session)

    if report:
        exporter = Exporter(cfg, output_dir=output_dir, verbose=_get_verbose(ctx))
        exporter.export_weekly_report(result)


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
    old_summary = result.get('summary', {})
    campaign_df = result.get('campaign_df')
    result['summary'] = engine._generate_summary(
        records, campaign_df, old_summary.get('filters')
    )
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
@click.option('--report', is_flag=True, help='同时生成对比周报')
@click.option('--a-date-from', 'a_date_from', type=str, default=None, help='A批筛选起始日期')
@click.option('--a-date-to', 'a_date_to', type=str, default=None, help='A批筛选截止日期')
@click.option('--a-channel', 'a_channel', type=str, default=None, help='A批按渠道筛选，逗号分隔')
@click.option('--a-campaign', 'a_campaign', type=str, default=None, help='A批按活动/批次标签筛选')
@click.option('--b-date-from', 'b_date_from', type=str, default=None, help='B批筛选起始日期')
@click.option('--b-date-to', 'b_date_to', type=str, default=None, help='B批筛选截止日期')
@click.option('--b-channel', 'b_channel', type=str, default=None, help='B批按渠道筛选，逗号分隔')
@click.option('--b-campaign', 'b_campaign', type=str, default=None, help='B批按活动/批次标签筛选')
@click.pass_context
def compare(ctx, a_file, b_file, history, following,
            label_a, label_b, output_dir, report,
            a_date_from, a_date_to, a_channel, a_campaign,
            b_date_from, b_date_to, b_channel, b_campaign):
    cfg = ctx.obj
    if _get_verbose(ctx):
        _print_banner()

    loader = DataLoader(cfg, verbose=_get_verbose(ctx))
    dedup_engine = DedupEngine(cfg, verbose=_get_verbose(ctx))
    session = _load_session()

    def _run_dedup_for(campaign_file, desc, d_from, d_to, fch, fcp):
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

        f_info = {}
        if d_from: f_info['起始日期'] = d_from
        if d_to: f_info['截止日期'] = d_to
        if fch: f_info['渠道'] = fch
        if fcp: f_info['活动/批次'] = fcp
        if f_info:
            c_df = _apply_filters(c_df, d_from, d_to, fch, fcp)
            if c_df is None or c_df.empty:
                click.echo(f"{Fore.RED}✗ {desc} 筛选后无数据，请放宽条件{Style.RESET_ALL}")
                sys.exit(1)
            click.echo(f"{Fore.YELLOW}🔍 {desc} 筛选后 {len(c_df)} 条线索{Style.RESET_ALL}")
        return dedup_engine.run(c_df, h_df, f_df, filters=f_info)

    if a_file:
        result_a = _run_dedup_for(a_file, label_a, a_date_from, a_date_to, a_channel, a_campaign)
    elif 'dedup_result' in session and session['dedup_result']:
        click.echo(f"{Fore.CYAN}▶ 使用会话中已有的 {label_a} 结果{Style.RESET_ALL}")
        result_a = session['dedup_result']
    else:
        click.echo(f"{Fore.RED}✗ 需要 --a-file 指定A批，或先执行 dedup{Style.RESET_ALL}")
        sys.exit(1)

    result_b = _run_dedup_for(b_file, label_b, b_date_from, b_date_to, b_channel, b_campaign)

    comparator = BatchComparator(cfg, verbose=_get_verbose(ctx))
    cmp_result = comparator.compare(result_a, result_b, label_a, label_b)
    comparator.export_comparison(cmp_result, output_dir, label_a, label_b)

    if report:
        comparator.export_compare_report(cmp_result, output_dir, label_a, label_b)


# ==================== report 命令 ====================
@cli.command(help="📊 生成运营周报：核心指标+各渠道挤水率+水分预警")
@click.option('--format', '-f', 'fmt', type=click.Choice(['both', 'xlsx', 'json']),
              default='both', help='输出格式: both=Excel+JSON, xlsx, json')
@click.option('--output', '-o', 'output_dir', type=click.Path(), default=None,
              help='输出目录')
@click.pass_context
def report(ctx, fmt, output_dir):
    cfg = ctx.obj
    if _get_verbose(ctx):
        _print_banner()

    session = _load_session()
    result = session.get('dedup_result')

    if result is None:
        click.echo(f"{Fore.RED}✗ 请先执行 dedup 命令{Style.RESET_ALL}")
        sys.exit(1)

    out_dir = output_dir or session.get('output_dir', 'output')
    exporter = Exporter(cfg, output_dir=out_dir, verbose=_get_verbose(ctx))

    if fmt in ('both', 'xlsx'):
        exporter.export_weekly_report(result)
    if fmt == 'json':
        summary = result['summary']
        counts = summary['counts']
        by_orig = summary.get('by_original_channel', {})
        report_data = {
            '生成时间': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            '总投放线索': counts.get('total', 0),
            '有效线索': counts.get('keep_count', 0),
            '老客复咨': counts.get('老客复咨', 0),
            '门店在跟': counts.get('在跟名单', 0),
            '待复核': counts.get('大概率重复', 0) + counts.get('疑似重复', 0),
            '挤水率%': counts.get('water_rate', 0),
            '各渠道明细': {ch: data for ch, data in by_orig.items()}
        }
        os.makedirs(out_dir, exist_ok=True)
        json_path = os.path.join(out_dir, f"运营周报_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        click.echo(f"{Fore.GREEN}✅ JSON周报: {json_path}{Style.RESET_ALL}")


def main():
    cli()


if __name__ == '__main__':
    main()
