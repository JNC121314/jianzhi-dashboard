#!/usr/bin/env python3
"""
简知分销 · v8 看板重新生成
新增：渠道名→主播名映射、贡献榜钻取、每日/产品图表钻取、渠道筛选器（账号+日期）
移除：账号流量占比环形图
"""

import pandas as pd, json
from pathlib import Path
from datetime import datetime, timezone, timedelta

# 北京时间
BJT = timezone(timedelta(hours=8))

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data/exports"
ACCOUNTS = ["毛毛矩阵", "抖音", "视频号", "严总"]
CHANNEL_MAP_PATH = PROJECT_DIR / "data/渠道吧.xlsx"


def load_channel_map():
    """加载渠道→主播映射表，返回 dict"""
    if not CHANNEL_MAP_PATH.exists():
        print("  ⚠️ 渠道映射表不存在，跳过匹配")
        return {}
    df = pd.read_excel(CHANNEL_MAP_PATH)
    mapping = {}
    for _, row in df.iterrows():
        channel = str(row["渠道"]).strip()
        anchor = str(row["主播"]).strip()
        if channel and anchor:
            mapping[channel] = anchor
    print(f"  渠道映射表: {len(mapping)} 条")
    return mapping


def map_channel(name, mapping):
    """将渠道名替换为主播名（未匹配则保留原名）"""
    name = str(name).strip()
    return mapping.get(name, name)


def agg_group(df, group_col):
    """通用分组聚合"""
    grp = df.groupby(group_col).agg(总订单=("订单id","count"), 付费单=("付费","sum")).reset_index()
    grp["未付费"] = grp["总订单"] - grp["付费单"]
    grp = grp.sort_values("总订单", ascending=False)
    return {str(r[group_col]): {"总订单":int(r["总订单"]),"付费单":int(r["付费单"]),"未付费":int(r["未付费"])}
            for _,r in grp.iterrows()}


def main():
    # ── 加载渠道映射 ──
    ch_map = load_channel_map()

    # ── 读取数据 ──
    all_data = []
    for acc in ACCOUNTS:
        files = []
        if (DATA_DIR / acc).exists():
            files = sorted((DATA_DIR / acc).glob("export_dist_order_*.xlsx")) + sorted((DATA_DIR / acc).glob("dist_order_*.xlsx"))
        if files:
            df = pd.read_excel(files[-1])
            df["账号"] = acc
            all_data.append(df)
            print(f"  {acc}: {len(df)} 条 ({files[-1].name})")

    if not all_data:
        print("❌ 无可用数据")
        return

    merged = pd.concat(all_data, ignore_index=True)

    def is_paid(val):
        try: return float(str(val).replace("￥","").replace(",","").strip() or 0) > 0
        except: return False

    merged["付费"] = merged["付款金额"].apply(is_paid)
    merged["日期"] = pd.to_datetime(merged["订单支付时间"], errors="coerce")
    merged["月份"] = merged["日期"].dt.to_period("M").astype(str)
    merged["日"] = merged["日期"].dt.date.astype(str)

    # ── 渠道名映射替换 ──
    merged["渠道名称"] = merged["渠道名称"].apply(lambda x: map_channel(x, ch_map))
    print(f"  渠道名已映射替换")

    months_all = sorted(merged["月份"].dropna().unique())
    print(f"  月份: {months_all[0]} ~ {months_all[-1]} ({len(months_all)}个月)")

    # ── 月度汇总 + 全部钻取数据 ──
    monthly_summary = {}

    for m in months_all:
        mdf = merged[merged["月份"] == m]
        ms = {}

        # 基础维度汇总
        for prefix, group_col in [("accounts","账号"),("products","产品名称"),("channels","渠道名称")]:
            ms[prefix] = agg_group(mdf, group_col)

        # 每日汇总
        daily_m = mdf.groupby("日").agg(总订单=("订单id","count"), 付费单=("付费","sum")).reset_index()
        daily_m["未付费"] = daily_m["总订单"] - daily_m["付费单"]
        ms["daily"] = {str(r["日"]): {"总订单":int(r["总订单"]),"付费单":int(r["付费单"]),"未付费":int(r["未付费"])}
                       for _,r in daily_m.iterrows()}

        # === 钻取数据 ===

        # 每日钻取
        daily_drill = {}
        for day, ddf in mdf.groupby("日"):
            day_entry = {
                "accounts": agg_group(ddf, "账号"),
                "products": agg_group(ddf, "产品名称"),
                "channels": agg_group(ddf, "渠道名称"),
            }
            # 产品→渠道（逐日）
            prod_ch = {}
            for prod, pdf in ddf.groupby("产品名称"):
                prod_ch[str(prod)] = agg_group(pdf, "渠道名称")
            day_entry["product_channels"] = prod_ch
            # 渠道→账号（逐日）
            ch_acc = {}
            for ch, cdf in ddf.groupby("渠道名称"):
                ch_acc[str(ch)] = agg_group(cdf, "账号")
            day_entry["channel_accounts"] = ch_acc
            # 产品→看板→主播（逐日，供量级速览L2/L3使用）
            prod_acc_st = {}
            for prod, pdf in ddf.groupby("产品名称"):
                pa = {}
                for acc, aaf in pdf.groupby("账号"):
                    st = {}
                    for streamer, sf in aaf.groupby("渠道名称"):
                        st[str(streamer)] = {"总订单":int(sf["订单id"].count()), "付费单":int(sf["付费"].sum()), "未付费":int(sf["订单id"].count()-sf["付费"].sum())}
                    pa[str(acc)] = st
                prod_acc_st[str(prod)] = pa
            day_entry["product_account_streamers"] = prod_acc_st
            daily_drill[str(day)] = day_entry
        ms["daily_drill"] = daily_drill

        # 产品钻取
        product_drill = {}
        for prod, pdf in mdf.groupby("产品名称"):
            product_drill[str(prod)] = {
                "accounts": agg_group(pdf, "账号"),
                "channels": agg_group(pdf, "渠道名称"),
            }
        ms["product_drill"] = product_drill

        # 渠道钻取
        channel_drill = {}
        for ch, cdf in mdf.groupby("渠道名称"):
            channel_drill[str(ch)] = {
                "products": agg_group(cdf, "产品名称"),
                "accounts": agg_group(cdf, "账号"),
            }
        ms["channel_drill"] = channel_drill

        # 账号钻取（新增）：每个账号 → {products, channels}
        account_drill = {}
        for acc, adf in mdf.groupby("账号"):
            account_drill[str(acc)] = {
                "products": agg_group(adf, "产品名称"),
                "channels": agg_group(adf, "渠道名称"),
            }
        ms["account_drill"] = account_drill

        # 账号×渠道（新增）：供渠道 Top10 账号筛选器
        account_channels = {}
        for acc in ACCOUNTS:
            adf = mdf[mdf["账号"] == acc]
            if len(adf) > 0:
                account_channels[acc] = agg_group(adf, "渠道名称")
            else:
                account_channels[acc] = {}
        ms["account_channels"] = account_channels

        # 账号×日×渠道（新增）：供渠道汇总日期筛选器
        account_daily_channels = {}
        for acc in ACCOUNTS:
            adf = mdf[mdf["账号"] == acc]
            acc_daily = {}
            for day, ddf in adf.groupby("日"):
                acc_daily[str(day)] = agg_group(ddf, "渠道名称")
            account_daily_channels[acc] = acc_daily
        ms["account_daily_channels"] = account_daily_channels

        monthly_summary[m] = ms

    # ── 贡献榜（全时段排名） ──
    contribution = []
    for acc in ACCOUNTS:
        adf = merged[merged["账号"] == acc]
        n = len(adf)
        p = int(adf["付费"].sum())
        if n > 0:
            contribution.append({
                "name": acc,
                "总订单": n,
                "付费单": p,
                "未付费": n - p,
                "付费率": round(p / n * 100, 1),
                "覆盖产品": adf["产品名称"].nunique(),
                "覆盖渠道": adf["渠道名称"].nunique(),
            })
    contribution.sort(key=lambda x: x["总订单"], reverse=True)

    # ── JSON ──
    data_json = json.dumps({
        "months": months_all,
        "currentMonth": months_all[-1],
        "accounts": ACCOUNTS,
        "monthly": monthly_summary,
        "contribution": contribution,
        "generatedAt": datetime.now(BJT).strftime("%Y-%m-%d %H:%M"),
    }, ensure_ascii=False)

    # ── 生成 HTML ──
    generate_html(data_json)
    print(f"✅ 看板已生成: data/exports/简知分销数据看板.html")


def generate_html(data_json):
    html = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>简知分销 · 日报看板</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
:root{--bg:#0f172a;--bg-card:#1e293b;--bg-hover:#273548;--border:#334155;--text:#e2e8f0;--text-muted:#94a3b8;--text-head:#cbd5e1;--accent:#3b82f6;--success:#22c55e;--warn:#f59e0b;--purple:#a855f7}
body.light{--bg:#f8fafc;--bg-card:#fff;--bg-hover:#f1f5f9;--border:#e2e8f0;--text:#1e293b;--text-muted:#64748b;--text-head:#334155}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:16px 24px;line-height:1.5;max-width:1280px;margin:0 auto}

.card{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin-bottom:16px}
.card h3{font-size:13px;color:var(--text-head);margin-bottom:10px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}

.header{text-align:center;padding:20px 0 8px}
.header h1{font-size:22px;color:var(--text);margin-bottom:4px}
.header p{color:var(--text-muted);font-size:12px}
.update-badge{display:inline-block;background:linear-gradient(135deg,var(--accent),#6366f1);color:#fff;padding:5px 16px;border-radius:20px;font-size:12px;font-weight:600;margin-top:10px;letter-spacing:.3px;white-space:nowrap}
body.light .update-badge{color:#fff}

.month-bar{display:flex;align-items:center;justify-content:center;gap:14px;margin:16px 0 20px;flex-wrap:wrap}
.month-bar select,.filter-select{background:var(--bg-card);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:7px 14px;font-size:13px;cursor:pointer;min-width:130px;appearance:none;text-align:center;text-align-last:center}
.month-bar select:focus,.filter-select:focus{outline:none;border-color:var(--accent)}
.month-nav{background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:7px 14px;cursor:pointer;color:var(--text);font-size:14px;transition:all .15s}
.month-nav:hover{background:var(--bg-hover);border-color:var(--accent)}
.month-range{color:var(--text-muted);font-size:11px}

.kpi-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
.kpi{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;padding:16px;transition:border-color .2s}
.kpi:hover{border-color:var(--accent)}
.kpi .label{color:var(--text-muted);font-size:11px;margin-bottom:6px}
.kpi .value{font-size:26px;font-weight:700}
.kpi .sub{font-size:10px;color:var(--text-muted);margin-top:4px}

/* 贡献榜 */
.rank-list{display:flex;flex-direction:column;gap:6px}
.rank-item{display:flex;align-items:center;gap:14px;padding:8px 10px;background:var(--bg);border-radius:8px;cursor:pointer;transition:background .15s;user-select:none}
.rank-item:hover{background:var(--bg-hover)}
.rank-item.expanded{background:var(--bg-hover);border:1px solid var(--accent)}
.rank-num{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0}
.rank-num.gold{background:linear-gradient(135deg,#f59e0b,#d97706);color:#fff}
.rank-num.silver{background:linear-gradient(135deg,#94a3b8,#64748b);color:#fff}
.rank-num.bronze{background:linear-gradient(135deg,#d97706,#92400e);color:#fff}
.rank-num.normal{background:var(--bg-hover);color:var(--text-muted)}
.rank-name{font-weight:600;font-size:13px;min-width:70px}
.rank-bar-wrap{flex:1;height:20px;background:var(--bg-hover);border-radius:10px;overflow:hidden}
.rank-bar{height:100%;border-radius:10px;transition:width .6s ease}
.rank-bar-inner{display:flex;gap:0;height:100%}
.rank-bar-paid{background:var(--success);opacity:.85;height:100%}
.rank-bar-free{background:var(--text-muted);opacity:.3;height:100%}
.rank-value{font-size:12px;font-weight:600;min-width:45px;text-align:right}
.rank-rate{font-size:10px;color:var(--text-muted);min-width:38px;text-align:right}
.rank-drill{display:none;padding:8px 12px 8px 56px;background:var(--bg);border-radius:0 0 8px 8px;margin-top:-2px}
.rank-drill.show{display:flex;gap:16px;flex-wrap:wrap}

/* 图表区 */
.charts-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:900px){.charts-grid{grid-template-columns:1fr}}
.chart-box{background:var(--bg-card);border:1px solid var(--border);border-radius:12px;padding:16px}
.chart-box.full{grid-column:1/-1}
.chart-box h3{font-size:13px;color:var(--text-head);margin-bottom:10px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.chart-box canvas{max-height:280px}
.chart-drill{display:none;margin-top:10px;padding:10px 14px;background:var(--bg);border-radius:8px}
.chart-drill.show{display:block}
.chart-drill h4{font-size:11px;color:var(--text-muted);margin-bottom:6px}

/* 筛选器行 */
.filter-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:0}
.filter-row label{font-size:11px;color:var(--text-muted)}

/* 表格 */
.section{margin-bottom:22px}
.section h2{font-size:14px;color:var(--text-head);margin-bottom:10px}
.table-wrap{overflow-x:auto;border-radius:10px;border:1px solid var(--border)}
table{width:100%;border-collapse:collapse;background:var(--bg-card);font-size:12px}
th,td{padding:8px 12px;text-align:left;white-space:nowrap}
th{background:var(--bg-hover);color:var(--text-muted);font-weight:600;cursor:pointer;user-select:none;border-bottom:2px solid var(--border)}
th:hover{color:var(--text)}
th .sort-icon{font-size:9px;margin-left:3px;opacity:.3}
tr:nth-child(even){background:var(--bg)}
tr:hover{background:var(--bg-hover)}

.drill-row{cursor:pointer}
.drill-row:hover td{color:var(--accent)}
.drill-row td:first-child::before{content:'▸ ';font-size:10px;margin-right:4px;color:var(--text-muted)}
.drill-row.expanded td:first-child::before{content:'▾ ';color:var(--accent)}
.drill-detail{display:none}
.drill-detail.show{display:table-row}
.drill-detail td{padding:0;background:var(--bg);border-bottom:1px solid var(--border)}
.drill-inner{padding:10px 16px;display:flex;gap:16px;flex-wrap:wrap}
.drill-col{flex:1;min-width:170px}
.drill-col h4{font-size:11px;color:var(--text-muted);margin-bottom:6px;font-weight:500}
.drill-col table{width:100%;font-size:11px}
.drill-col td,.drill-col th{padding:4px 8px}
.drill-col th{background:transparent;border-bottom:1px solid var(--border);font-size:10px}
.drill-col tr:nth-child(even){background:transparent}

.tooltip-hint{font-size:10px;color:var(--text-muted);font-weight:400;margin-left:6px}
.footer{text-align:center;color:var(--text-muted);font-size:11px;padding:20px 0 10px}
.theme-toggle{position:fixed;top:16px;right:20px;z-index:100}
.theme-toggle button{background:var(--bg-card);border:1px solid var(--border);color:var(--text);border-radius:20px;padding:6px 14px;cursor:pointer;font-size:12px}
.theme-toggle button:hover{border-color:var(--accent)}

/* ── 量级速览（分级树） ── */
.ov-wrap{border-radius:10px;border:1px solid var(--border);overflow:hidden;background:var(--bg-card)}
.ov-tree{width:100%;border-collapse:collapse;font-size:12px}
.ov-tree th{background:var(--bg-hover);color:var(--text-muted);font-weight:600;padding:9px 12px;border-bottom:2px solid var(--border);white-space:nowrap;font-size:11px;text-align:left}
.ov-tree th.ov-num{text-align:right}
.ov-tree td{padding:7px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
.ov-tree tr:last-child td{border-bottom:none}
.ov-l1{font-weight:700;cursor:pointer;transition:background .12s}
.ov-l1:hover{background:var(--bg-hover)}
.ov-l1 td:first-child::before{content:'▸ ';font-size:10px;color:var(--text-muted);margin-right:4px}
.ov-l1.exp td:first-child::before{content:'▾ ';color:var(--accent)}
.ov-l2{font-weight:500;cursor:pointer;background:var(--bg);transition:background .12s}
.ov-l2:hover{background:var(--bg-hover)}
.ov-l2 td:first-child{padding-left:28px}
.ov-l2 td:first-child::before{content:'▸ ';font-size:9px;color:var(--text-muted);margin-right:3px}
.ov-l2.exp td:first-child::before{content:'▾ ';color:var(--accent)}
.ov-l3{font-weight:400;color:var(--text-muted);background:var(--bg)}
.ov-l3 td:first-child{padding-left:48px;font-size:11px}
.ov-total{background:linear-gradient(90deg,var(--bg-hover),transparent) !important;font-weight:700}
.ov-total td{padding:10px 12px !important;font-size:13px}
.ov-num{text-align:right;font-variant-numeric:tabular-nums;font-weight:600;font-size:13px}
.ov-chg{text-align:right;font-variant-numeric:tabular-nums;font-size:11px;font-weight:600;min-width:50px}
.ov-date-sel{background:var(--bg-card);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:7px 14px;font-size:13px;cursor:pointer;min-width:130px;appearance:none;text-align:center;text-align-last:center}
.ov-date-sel:focus{outline:none;border-color:var(--accent)}
.ov-range{font-size:11px;color:var(--text-muted);padding:4px 10px;background:var(--bg);border-radius:6px;font-weight:500}
.ov-empty{color:var(--text-muted);font-size:12px;padding:24px;text-align:center}
.ov-hide{display:none}
</style>
</head>
<body>
<div class="theme-toggle"><button onclick="toggleTheme()" id="tbtn">🌙</button></div>
<div class="header"><h1>简知分销 · 日报看板</h1><p id="headerInfo"></p><div class="update-badge" id="updateBadge"></div></div>

<div class="month-bar">
  <button class="month-nav" onclick="prevMonth()">◀</button>
  <select id="monthSelect" onchange="onMonthChange()"></select>
  <button class="month-nav" onclick="nextMonth()">▶</button>
  <span class="month-range" id="monthRange"></span>
  <select class="ov-date-sel" id="ovDateSelect" onchange="renderOverview()" title="选择对比日期"></select>
  <span class="ov-range" id="ovDateRange"></span>
</div>

<!-- 量级速览：项目→渠道→主播分级树 -->
<div class="section" id="ovSection">
  <h2>📊 量级速览 <span class="tooltip-hint">💡 点击项目→渠道→主播逐级展开</span></h2>
  <div class="ov-wrap">
    <table class="ov-tree" id="ovTree">
      <thead><tr><th style="width:36%">项目 · 渠道 · 主播</th><th class="ov-num" id="ovPrevHead" style="width:16%">前日</th><th class="ov-num" id="ovCurrHead" style="width:16%">当日</th><th class="ov-chg" style="width:12%">变化</th></tr></thead>
      <tbody id="ovBody"></tbody>
    </table>
  </div>
</div>

<div class="kpi-grid" id="kpiGrid"></div>

<!-- 每日明细 -->
<div class="section">
  <h2>📅 每日明细 <span class="tooltip-hint">💡 点击行展开当日各维度分布</span></h2>
  <div class="table-wrap"><table id="tblDaily">
    <thead><tr>
      <th onclick="sortTbl('tblDaily',0)">日期<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblDaily',1)">总线索<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblDaily',2)">付费<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblDaily',3)">未付费<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblDaily',4)">付费率<span class="sort-icon">↕</span></th>
    </tr></thead><tbody id="dailyTbody"></tbody></table></div>
</div>

<!-- 贡献榜（可钻取） -->
<div class="card">
  <h3>🏆 月度线索贡献榜 <span class="tooltip-hint">💡 点击展开查看账号在各产品/渠道的分布</span></h3>
  <div class="rank-list" id="rankList"></div>
</div>

<!-- 图表区 -->
<div class="charts-grid">
  <div class="chart-box full">
    <h3>📈 每日线索 · 项目分布 <span class="tooltip-hint">💡 点击柱子钻取当日明细</span></h3>
    <canvas id="chartDaily"></canvas>
    <div class="chart-drill" id="drillChartDaily"></div>
  </div>
  <div class="chart-box">
    <h3>🔗 渠道 Top 10</h3>
    <div class="filter-row">
      <label>项目：</label>
      <select class="filter-select" id="chFilterAcc" onchange="renderChannelChart(currentMd())">
        <option value="all">全部账号</option>
      </select>
    </div>
    <canvas id="chartChannel"></canvas>
  </div>
  <div class="chart-box">
    <h3>🎁 产品 Top 12 <span class="tooltip-hint">💡 点击横条钻取</span></h3>
    <canvas id="chartProduct"></canvas>
    <div class="chart-drill" id="drillChartProduct"></div>
  </div>
</div>

<!-- 产品汇总 -->
<div class="section">
  <h2>🎁 产品汇总 <span class="tooltip-hint">💡 点击行展开产品在各账号/渠道分布</span></h2>
  <div class="table-wrap"><table id="tblProd">
    <thead><tr>
      <th onclick="sortTbl('tblProd',0)">产品名称<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblProd',1)">总线索<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblProd',2)">付费<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblProd',3)">未付费<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblProd',4)">付费率<span class="sort-icon">↕</span></th>
    </tr></thead><tbody id="prodTbody"></tbody></table></div>
</div>

<!-- 渠道汇总（含日期+项目筛选） -->
<div class="section">
  <h2>🔗 渠道汇总
    <span class="tooltip-hint">💡 点击行展开渠道在各产品/账号分布</span>
  </h2>
  <div class="filter-row" style="margin-bottom:10px">
    <label>项目：</label>
    <select class="filter-select" id="chTblFilterAcc" onchange="renderChannelTable(currentMd())">
      <option value="all">全部账号</option>
    </select>
    <label style="margin-left:12px">日期：</label>
    <select class="filter-select" id="chTblFilterDay" onchange="renderChannelTable(currentMd())">
      <option value="all">全部日期</option>
    </select>
  </div>
  <div class="table-wrap"><table id="tblCh">
    <thead><tr>
      <th onclick="sortTbl('tblCh',0)">渠道名称<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblCh',1)">总线索<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblCh',2)">付费<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblCh',3)">未付费<span class="sort-icon">↕</span></th>
      <th onclick="sortTbl('tblCh',4)">付费率<span class="sort-icon">↕</span></th>
    </tr></thead><tbody id="chTbody"></tbody></table></div>
</div>

<div class="footer">简知分销 · 日报看板 v8 · <span id="genTime"></span></div>

<script>
const DATA=__DATA_JSON__;
const MONTHS=DATA.months, ACCOUNTS=DATA.accounts;
const COLORS=['#6366f1','#22c55e','#f59e0b','#ec4899','#06b6d4','#f97316','#8b5cf6','#14b8a6','#ef4444','#e11d48','#0891b2','#7c3aed'];

let currentMonth=DATA.currentMonth, charts={}, theme=localStorage.getItem('jz8-theme')||'dark';
let drillCache={};
let lastDailyDrillDay=null, lastProductDrillId=null;

function currentMd(){return DATA.monthly[currentMonth]}

function init(){
  document.getElementById('genTime').textContent='生成: '+DATA.generatedAt;
  document.getElementById('updateBadge').textContent='🕐 数据更新: '+DATA.generatedAt;
  if(theme==='light')document.body.classList.add('light');
  document.getElementById('tbtn').textContent=theme==='light'?'☀️':'🌙';
  let sel=document.getElementById('monthSelect');
  MONTHS.forEach(function(m){sel.appendChild(new Option(m,m))});
  sel.value=currentMonth;
  // 填充账号筛选器
  let opts='<option value="all">全部账号</option>';
  ACCOUNTS.forEach(function(a){opts+='<option value="'+a+'">'+a+'</option>'});
  ['chFilterAcc','chTblFilterAcc'].forEach(function(id){document.getElementById(id).innerHTML=opts});
  // 事件委托：表格钻取
  ['dailyTbody','prodTbody','chTbody'].forEach(function(id){
    document.getElementById(id).addEventListener('click',function(e){
      let row=e.target.closest('.drill-row');
      if(!row)return;
      let type=row.dataset.drillType, key=decodeURIComponent(row.dataset.drillKey);
      toggleTableDrill(row,type,key);
    });
  });
  // 事件委托：贡献榜钻取
  document.getElementById('rankList').addEventListener('click',function(e){
    let item=e.target.closest('.rank-item');
    if(!item)return;
    toggleRankDrill(item);
  });
  renderAll();
}

function renderAll(){
  let md=currentMd(); if(!md)return;
  let total={}; total.总订单=0; total.付费单=0; total.未付费=0;
  Object.values(md.accounts).forEach(function(a){total.总订单+=a.总订单;total.付费单+=a.付费单;total.未付费+=a.未付费});
  let rate=total.总订单>0?(total.付费单/total.总订单*100).toFixed(1):'0.0';
  document.getElementById('headerInfo').textContent=currentMonth+' · '+total.总订单.toLocaleString()+'条线索 · 付费率 '+rate+'%';
  document.getElementById('monthRange').textContent=MONTHS[0]+' ~ '+MONTHS[MONTHS.length-1];
  drillCache={};
  lastDailyDrillDay=null; lastProductDrillId=null;
  renderKPI(total,rate,md);
  renderRanking(md,total);
  renderDailyChart(md);
  renderChannelChart(md);
  renderProductChart(md);
  renderDailyTable(md);
  renderProductTable(md);
  renderChannelTable(md);
  updateChTblDayFilter(md);
  initOverview();
  renderOverview();
}

// ── KPI ──
function renderKPI(total,rate,md){
  document.getElementById('kpiGrid').innerHTML=
    mkKPI('总线索',total.总订单.toLocaleString(),'var(--accent)',currentMonth)+
    mkKPI('付费线索',total.付费单.toLocaleString(),'var(--success)','付费率 '+rate+'%')+
    mkKPI('未付费线索',total.未付费.toLocaleString(),'var(--warn)','获客流量')+
    mkKPI('覆盖产品',Object.keys(md.products||{}).length,'var(--purple)','渠道 '+Object.keys(md.channels||{}).length+' 个');
}
function mkKPI(label,value,color,sub){return'<div class="kpi"><div class="label">'+label+'</div><div class="value" style="color:'+color+'">'+value+'</div><div class="sub">'+sub+'</div></div>'}

// ── 贡献榜（可钻取） ──
function renderRanking(md,total){
  let accs=ACCOUNTS.filter(function(a){return md.accounts[a]&&md.accounts[a].总订单>0});
  accs.sort(function(a,b){return md.accounts[b].总订单-md.accounts[a].总订单});
  let maxN=accs.length>0?md.accounts[accs[0]].总订单:1;
  let rankClass=['gold','silver','bronze','normal'];
  let h='';
  accs.forEach(function(a,i){
    let d=md.accounts[a], pct=Math.round(d.总订单/maxN*100);
    let paidPct=d.总订单>0?Math.round(d.付费单/d.总订单*100):0;
    let rk=i<3?rankClass[i]:'normal';
    let dt=md.account_drill&&md.account_drill[a];
    let drillId='rank-drill-'+a;
    h+='<div class="rank-item" data-rank-acc="'+a+'">'+
      '<div class="rank-num '+rk+'">'+(i+1)+'</div>'+
      '<div class="rank-name">'+a+'</div>'+
      '<div class="rank-bar-wrap" title="付费 '+d.付费单.toLocaleString()+' · 未付费 '+d.未付费.toLocaleString()+'">'+
        '<div class="rank-bar" style="width:'+pct+'%">'+
          '<div class="rank-bar-inner"><div class="rank-bar-paid" style="width:'+paidPct+'%"></div><div class="rank-bar-free" style="width:'+(100-paidPct)+'%"></div></div>'+
        '</div>'+
      '</div>'+
      '<div class="rank-value">'+d.总订单.toLocaleString()+'</div>'+
      '<div class="rank-rate">'+paidPct+'%</div>'+
    '</div>'+
    '<div class="rank-drill" id="'+drillId+'"></div>';
  });
  if(!h) h='<div style="color:var(--text-muted);font-size:12px;padding:8px">本月暂无数据</div>';
  document.getElementById('rankList').innerHTML=h;
}

function toggleRankDrill(item){
  let acc=item.dataset.rankAcc;
  let drillEl=item.nextElementSibling;
  if(!drillEl||!drillEl.classList.contains('rank-drill'))return;
  let isOpen=drillEl.classList.contains('show');
  // 关闭所有
  document.querySelectorAll('.rank-drill.show').forEach(function(el){el.classList.remove('show')});
  document.querySelectorAll('.rank-item.expanded').forEach(function(el){el.classList.remove('expanded')});
  if(!isOpen){
    let cacheKey='rank:'+acc;
    if(!drillCache[cacheKey]){
      drillCache[cacheKey]=buildRankDrill(acc);
    }
    drillEl.innerHTML=drillCache[cacheKey];
    drillEl.classList.add('show');
    item.classList.add('expanded');
  }
}

function buildRankDrill(acc){
  let md=currentMd();
  let d=md.account_drill&&md.account_drill[acc];
  let prods=Object.entries(d?d.products:{}).sort(function(a,b){return b[1].总订单-a[1].总订单});
  let chs=Object.entries(d?d.channels:{}).sort(function(a,b){return b[1].总订单-a[1].总订单});
  let html='';
  html+=drillCol('按产品 (Top 8)',prods.slice(0,8));
  html+=drillCol('按渠道 (Top 8)',chs.slice(0,8));
  return html||'<span style="color:var(--text-muted);font-size:11px">暂无钻取数据</span>';
}

function drillCol(title,entries){
  if(!entries.length)return'';
  let h='<div class="drill-col"><h4>'+title+'</h4><table><tr><th>名称</th><th>线索</th><th>付费</th><th>未付费</th></tr>';
  entries.forEach(function(e){h+='<tr><td>'+e[0]+'</td><td>'+e[1].总订单.toLocaleString()+'</td><td style="color:var(--success)">'+e[1].付费单.toLocaleString()+'</td><td style="color:var(--text-muted)">'+e[1].未付费.toLocaleString()+'</td></tr>'});
  return h+'</table></div>';
}

// ── 图表：每日线索项目分布（可钻取） ──
function renderDailyChart(md){
  dc('daily');
  let days=Object.keys(md.daily).sort();
  let labels=days.map(function(d){return d.slice(5)});
  let datasets=[];
  ACCOUNTS.forEach(function(a,i){
    let data=days.map(function(d){
      let dd=md.daily_drill&&md.daily_drill[d]&&md.daily_drill[d].accounts[a];
      return dd?dd.总订单:0;
    });
    datasets.push({label:a,data:data,backgroundColor:COLORS[i%COLORS.length],stack:'s1',borderRadius:2});
  });
  charts.daily=new Chart(document.getElementById('chartDaily'),{
    type:'bar',data:{labels:labels,datasets:datasets},
    options:{
      responsive:true,maintainAspectRatio:true,
      onClick:function(e,elts){if(elts.length>0){let idx=elts[0].index,dsIdx=elts[0].datasetIndex;showDailyDrill(days[idx],datasets[dsIdx].label,md)}},
      plugins:{
        legend:{labels:{color:clr(),padding:12,usePointStyle:true,pointStyleWidth:8,font:{size:11}}},
        tooltip:{callbacks:{label:function(ctx){return ctx.dataset.label+': '+ctx.raw.toLocaleString()+'条'}}}
      },
      scales:{
        x:{ticks:{color:clr(),maxRotation:45,font:{size:10}},grid:{color:cg()},stacked:true},
        y:{ticks:{color:clr(),font:{size:10}},grid:{color:cg()},stacked:true,title:{display:true,text:'线索数',color:clr()}}
      }
    }
  });
}

function showDailyDrill(day,acc,md){
  let el=document.getElementById('drillChartDaily');
  if(lastDailyDrillDay===day&&el.classList.contains('show')){
    el.classList.remove('show');
    lastDailyDrillDay=null;
    return;
  }
  let dd=md.daily_drill&&md.daily_drill[day];
  if(!dd)return;
  lastDailyDrillDay=day;
  let prods=Object.entries(dd.products||{}).sort(function(a,b){return b[1].总订单-a[1].总订单});
  let chs=Object.entries(dd.channels||{}).sort(function(a,b){return b[1].总订单-a[1].总订单});
  let accts=Object.entries(dd.accounts||{}).sort(function(a,b){return b[1].总订单-a[1].总订单});
  el.innerHTML='<h4>'+day+' · 各维度分布</h4>'+drillCol('按账号',accts.slice(0,5))+drillCol('按产品',prods.slice(0,5))+drillCol('按渠道',chs.slice(0,5));
  el.classList.add('show');
  setTimeout(function(){el.scrollIntoView({behavior:'smooth',block:'nearest'})},100);
}

// ── 图表：渠道 Top 10（含项目筛选） ──
function renderChannelChart(md){
  dc('ch');
  let filterAcc=document.getElementById('chFilterAcc').value;
  let chData;
  if(filterAcc==='all'){
    chData=md.channels||{};
  }else{
    chData=(md.account_channels&&md.account_channels[filterAcc])||{};
  }
  let chs=Object.entries(chData).sort(function(a,b){return b[1].总订单-a[1].总订单}).slice(0,10);
  if(chs.length===0)return;
  let colors=chs.map(function(_,i){return COLORS[(i+2)%COLORS.length]});
  charts.ch=new Chart(document.getElementById('chartChannel'),{
    type:'bar',data:{labels:chs.map(function(c){return c[0]}),datasets:[{label:'线索数',data:chs.map(function(c){return c[1].总订单}),backgroundColor:colors,borderRadius:4}]},
    options:{
      responsive:true,maintainAspectRatio:true,indexAxis:'y',
      plugins:{legend:{display:false},tooltip:{callbacks:{label:function(ctx){return ctx.raw.toLocaleString()+'条'}}}},
      scales:{
        x:{ticks:{color:clr(),font:{size:10}},grid:{color:cg()}},
        y:{ticks:{color:clr(),font:{size:10},callback:function(v){return v.length>14?v.slice(0,12)+'..':v}},grid:{display:false}}
      }
    }
  });
}

// ── 图表：产品 Top 12（可钻取） ──
function renderProductChart(md){
  dc('prod');
  let prods=Object.entries(md.products||{}).sort(function(a,b){return b[1].总订单-a[1].总订单}).slice(0,12);
  if(prods.length===0)return;
  let colors=prods.map(function(_,i){return COLORS[i%COLORS.length]});
  charts.prod=new Chart(document.getElementById('chartProduct'),{
    type:'bar',data:{labels:prods.map(function(p){return p[0]}),datasets:[{label:'线索数',data:prods.map(function(p){return p[1].总订单}),backgroundColor:colors,borderRadius:4}]},
    options:{
      responsive:true,maintainAspectRatio:true,indexAxis:'y',
      onClick:function(e,elts){if(elts.length>0){let idx=elts[0].index;showProductDrill(prods[idx][0],md)}},
      plugins:{legend:{display:false},tooltip:{callbacks:{label:function(ctx){return ctx.raw.toLocaleString()+'条'}}}},
      scales:{
        x:{ticks:{color:clr(),font:{size:10}},grid:{color:cg()}},
        y:{ticks:{color:clr(),font:{size:10},callback:function(v){return v.length>18?v.slice(0,16)+'..':v}},grid:{display:false}}
      }
    }
  });
}

function showProductDrill(prod,md){
  let el=document.getElementById('drillChartProduct');
  if(lastProductDrillId===prod&&el.classList.contains('show')){
    el.classList.remove('show');
    lastProductDrillId=null;
    return;
  }
  let d=md.product_drill&&md.product_drill[prod];
  if(!d)return;
  lastProductDrillId=prod;
  let accts=Object.entries(d.accounts||{}).sort(function(a,b){return b[1].总订单-a[1].总订单});
  let chs=Object.entries(d.channels||{}).sort(function(a,b){return b[1].总订单-a[1].总订单});
  el.innerHTML='<h4>'+prod+' · 各维度分布</h4>'+drillCol('按账号',accts.slice(0,6))+drillCol('按渠道',chs.slice(0,6));
  el.classList.add('show');
  setTimeout(function(){el.scrollIntoView({behavior:'smooth',block:'nearest'})},100);
}

// ── 图表工具 ──
function dc(k){if(charts[k]){charts[k].destroy();charts[k]=null}}
function clr(){return theme==='light'?'#64748b':'#94a3b8'}
function cg(){return theme==='light'?'#e2e8f0':'#334155'}

// ── 每日明细表 ──
function renderDailyTable(md){
  let days=Object.keys(md.daily).sort().reverse();
  document.getElementById('dailyTbody').innerHTML=days.map(function(d){
    let v=md.daily[d],r=v.总订单>0?(v.付费单/v.总订单*100).toFixed(1)+'%':'0%';
    let sid=d.replace(/[^a-zA-Z0-9]/g,'_');
    return'<tr class="drill-row" data-drill-type="daily" data-drill-key="'+encodeURIComponent(d)+'">'+
      '<td>'+d+'</td><td>'+v.总订单.toLocaleString()+'</td><td>'+v.付费单.toLocaleString()+'</td><td>'+v.未付费.toLocaleString()+'</td><td>'+r+'</td>'+
      '</tr><tr class="drill-detail" id="dd-'+sid+'"><td colspan="5"><div class="drill-inner" id="ddi-'+sid+'"></div></td></tr>';
  }).join('');
}

// ── 产品汇总表 ──
function renderProductTable(md){
  let prods=Object.entries(md.products||{}).sort(function(a,b){return b[1].总订单-a[1].总订单});
  document.getElementById('prodTbody').innerHTML=prods.map(function(e){
    let k=e[0],v=e[1],r=v.总订单>0?(v.付费单/v.总订单*100).toFixed(1)+'%':'0%';
    let id=hashId(k);
    return'<tr class="drill-row" data-drill-type="product" data-drill-key="'+encodeURIComponent(k)+'">'+
      '<td>'+k+'</td><td>'+v.总订单.toLocaleString()+'</td><td>'+v.付费单.toLocaleString()+'</td><td>'+v.未付费.toLocaleString()+'</td><td>'+r+'</td>'+
      '</tr><tr class="drill-detail" id="dd-'+id+'"><td colspan="5"><div class="drill-inner" id="ddi-'+id+'"></div></td></tr>';
  }).join('');
}

// ── 渠道汇总表（含日期+项目筛选器） ──
function updateChTblDayFilter(md){
  let filterAcc=document.getElementById('chTblFilterAcc').value;
  let days=[];
  if(filterAcc==='all'){
    days=Object.keys(md.daily||{}).sort().reverse();
  }else{
    let adc=md.account_daily_channels&&md.account_daily_channels[filterAcc];
    days=adc?Object.keys(adc).sort().reverse():[];
  }
  let sel=document.getElementById('chTblFilterDay');
  let currentVal=sel.value;
  sel.innerHTML='<option value="all">全部日期</option>';
  days.forEach(function(d){sel.appendChild(new Option(d,d))});
  sel.value=currentVal||'all';
}

function renderChannelTable(md){
  let filterAcc=document.getElementById('chTblFilterAcc').value;
  let filterDay=document.getElementById('chTblFilterDay').value;
  let chData;
  if(filterAcc==='all'){
    if(filterDay==='all'){
      chData=md.channels||{};
    }else{
      let dd=md.daily_drill&&md.daily_drill[filterDay];
      chData=dd?dd.channels:{};
    }
  }else{
    if(filterDay==='all'){
      chData=(md.account_channels&&md.account_channels[filterAcc])||{};
    }else{
      let adc=md.account_daily_channels&&md.account_daily_channels[filterAcc];
      chData=(adc&&adc[filterDay])||{};
    }
  }
  let chs=Object.entries(chData).sort(function(a,b){return b[1].总订单-a[1].总订单});
  document.getElementById('chTbody').innerHTML=chs.map(function(e){
    let k=e[0],v=e[1],r=v.总订单>0?(v.付费单/v.总订单*100).toFixed(1)+'%':'0%';
    let id=hashId(k);
    return'<tr class="drill-row" data-drill-type="channel" data-drill-key="'+encodeURIComponent(k)+'">'+
      '<td>'+k+'</td><td>'+v.总订单.toLocaleString()+'</td><td>'+v.付费单.toLocaleString()+'</td><td>'+v.未付费.toLocaleString()+'</td><td>'+r+'</td>'+
      '</tr><tr class="drill-detail" id="dd-'+id+'"><td colspan="5"><div class="drill-inner" id="ddi-'+id+'"></div></td></tr>';
  }).join('');
}

// ── 量级速览：项目 → 渠道(看板) → 主播（三级树） ──
let ovTargetDate=null, ovExpL1=null, ovExpL2=null;

function getDayOfWeek(dateStr){
  var d=new Date(dateStr.replace(/-/g,'/'));
  return ['周日','周一','周二','周三','周四','周五','周六'][d.getDay()];
}
function formatDateShort(dateStr){return dateStr.slice(5)}

function initOverview(){
  var md=currentMd(); if(!md)return;
  var days=Object.keys(md.daily||{}).sort().reverse();
  var sel=document.getElementById('ovDateSelect');
  sel.innerHTML='';
  if(days.length===0){
    sel.innerHTML='<option value="">暂无数据</option>';
    document.getElementById('ovBody').innerHTML='<tr><td colspan="4" class="ov-empty">本月暂无数据</td></tr>';
    return;
  }
  if(!ovTargetDate||days.indexOf(ovTargetDate)<0) ovTargetDate=days[0];
  days.forEach(function(d){
    var opt=new Option(formatDateShort(d)+' '+getDayOfWeek(d),d);
    if(d===ovTargetDate) opt.selected=true;
    sel.appendChild(opt);
  });
}

function renderOverview(){
  var md=currentMd(); if(!md||!ovTargetDate) return;
  var allDays=Object.keys(md.daily||{}).sort();
  var targetIdx=allDays.indexOf(ovTargetDate);
  if(targetIdx<0){initOverview();return;}
  var prevDate=targetIdx>0?allDays[targetIdx-1]:null;

  // 更新表头日期
  document.getElementById('ovCurrHead').textContent=formatDateShort(ovTargetDate)+' '+getDayOfWeek(ovTargetDate);
  document.getElementById('ovPrevHead').textContent=prevDate?formatDateShort(prevDate)+' '+getDayOfWeek(prevDate):'—';

  // 日期范围标注
  var rangeEl=document.getElementById('ovDateRange');
  if(prevDate){
    rangeEl.textContent=formatDateShort(prevDate)+'('+getDayOfWeek(prevDate)+') vs '+formatDateShort(ovTargetDate)+'('+getDayOfWeek(ovTargetDate)+')';
  }else{
    rangeEl.textContent=formatDateShort(ovTargetDate)+'('+getDayOfWeek(ovTargetDate)+')（无前日）';
  }

  var targetDrill=md.daily_drill&&md.daily_drill[ovTargetDate];
  var prevDrill=prevDate?(md.daily_drill&&md.daily_drill[prevDate]):null;

  // 三级树：项目 → 渠道(看板账号) → 主播
  var html='';
  var totalT=0,totalP=0;

  // L1: 项目（产品名称）
  var tProds=targetDrill&&targetDrill.products?targetDrill.products:{};
  var pProds=prevDrill&&prevDrill.products?prevDrill.products:{};
  var prodNames=Object.keys(tProds).concat(Object.keys(pProds));
  prodNames=[...new Set(prodNames)];
  prodNames.sort(function(a,b){return(tProds[b]?tProds[b].总订单:0)-(tProds[a]?tProds[a].总订单:0)});

  prodNames.forEach(function(prodName){
    var tv=tProds[prodName]?tProds[prodName].总订单:0;
    var pv=pProds[prodName]?pProds[prodName].总订单:0;
    if(tv===0&&pv===0)return;
    totalT+=tv; totalP+=pv;
    var chg=calcChange(pv,tv);
    var isL1Exp=(ovExpL1===prodName);

    html+='<tr class="ov-l1'+(isL1Exp?' exp':'')+'" onclick="toggleOvL1(\\''+encodeURIComponent(prodName)+'\\')">'+
      '<td>'+prodName+'</td>'+
      '<td class="ov-num">'+(pv>0?pv.toLocaleString():'—')+'</td>'+
      '<td class="ov-num">'+(tv>0?tv.toLocaleString():'—')+'</td>'+
      '<td class="ov-chg">'+chgBadge(chg)+'</td></tr>';

    // L2: 渠道（看板账号）
    if(isL1Exp){
      // 该项目下各看板账号的数据
      var tAccs=targetDrill&&targetDrill.accounts?targetDrill.accounts:{};
      var pAccs=prevDrill&&prevDrill.accounts?prevDrill.accounts:{};

      // 通过 product_account_streamers 获取该项目下有哪些账号
      var tPASt=targetDrill&&targetDrill.product_account_streamers&&targetDrill.product_account_streamers[prodName]?targetDrill.product_account_streamers[prodName]:{};
      var pPASt=prevDrill&&prevDrill.product_account_streamers&&prevDrill.product_account_streamers[prodName]?prevDrill.product_account_streamers[prodName]:{};
      var accNames=Object.keys(tPASt).concat(Object.keys(pPASt));
      accNames=[...new Set(accNames)];
      accNames.sort(function(a,b){
        var tva=tPASt[a]?Object.values(tPASt[a]).reduce(function(s,v){return s+v.总订单},0):0;
        var tvb=tPASt[b]?Object.values(tPASt[b]).reduce(function(s,v){return s+v.总订单},0):0;
        return tvb-tva;
      });

      accNames.forEach(function(accName){
        // 该项目→该账号的合计
        var tvSt=tPASt[accName]?Object.values(tPASt[accName]).reduce(function(s,v){return s+v.总订单},0):0;
        var pvSt=pPASt[accName]?Object.values(pPASt[accName]).reduce(function(s,v){return s+v.总订单},0):0;
        var tvPaid=tPASt[accName]?Object.values(tPASt[accName]).reduce(function(s,v){return s+v.付费单},0):0;
        var pvPaid=pPASt[accName]?Object.values(pPASt[accName]).reduce(function(s,v){return s+v.付费单},0):0;
        if(tvSt===0&&pvSt===0)return;
        var chg2=calcChange(pvSt,tvSt);
        var isL2Exp=(ovExpL2===prodName+'|'+accName);

        html+='<tr class="ov-l2'+(isL2Exp?' exp':'')+'" onclick="toggleOvL2(\\''+encodeURIComponent(prodName)+'\\',\\''+encodeURIComponent(accName)+'\\')">'+
          '<td>'+accName+'</td>'+
          '<td class="ov-num">'+(pvSt>0?pvSt.toLocaleString():'—')+'</td>'+
          '<td class="ov-num">'+(tvSt>0?tvSt.toLocaleString():'—')+'</td>'+
          '<td class="ov-chg">'+chgBadge(chg2)+'</td></tr>';

        // L3: 主播名
        if(isL2Exp){
          var tStreamers=tPASt[accName]?tPASt[accName]:{};
          var pStreamers=pPASt[accName]?pPASt[accName]:{};
          var streamerNames=Object.keys(tStreamers).concat(Object.keys(pStreamers));
          streamerNames=[...new Set(streamerNames)];
          streamerNames.sort(function(a,b){return(tStreamers[b]?tStreamers[b].总订单:0)-(tStreamers[a]?tStreamers[a].总订单:0)});

          streamerNames.forEach(function(streamerName){
            var tv3=tStreamers[streamerName]?tStreamers[streamerName].总订单:0;
            var pv3=pStreamers[streamerName]?pStreamers[streamerName].总订单:0;
            if(tv3===0&&pv3===0)return;
            var chg3=calcChange(pv3,tv3);
            html+='<tr class="ov-l3">'+
              '<td>'+streamerName+'</td>'+
              '<td class="ov-num">'+(pv3>0?pv3.toLocaleString():'—')+'</td>'+
              '<td class="ov-num">'+(tv3>0?tv3.toLocaleString():'—')+'</td>'+
              '<td class="ov-chg">'+chgBadge(chg3)+'</td></tr>';
          });
        }
      });
    }
  });

  // 合计行
  html+='<tr class="ov-total"><td>合计</td>'+
    '<td class="ov-num">'+totalP.toLocaleString()+'</td>'+
    '<td class="ov-num">'+totalT.toLocaleString()+'</td>'+
    '<td class="ov-chg">'+chgBadge(calcChange(totalP,totalT))+'</td></tr>';

  document.getElementById('ovBody').innerHTML=html||'<tr><td colspan="4" class="ov-empty">该日期暂无数据</td></tr>';
}

function toggleOvL1(name){
  name=decodeURIComponent(name);
  ovExpL1=(ovExpL1===name)?null:name;
  ovExpL2=null; // 关闭L1时也关闭L2
  renderOverview();
}

function toggleOvL2(l1,l2){
  l1=decodeURIComponent(l1); l2=decodeURIComponent(l2);
  var key=l1+'|'+l2;
  ovExpL2=(ovExpL2===key)?null:key;
  renderOverview();
}

function calcChange(prev,curr){
  if(prev===0){return curr>0?{pct:'NEW',cls:'up'}:{pct:'0%',cls:'flat'};}
  var pct=((curr-prev)/prev*100);
  var sign=pct>=0?'+':'';
  return {pct:sign+pct.toFixed(0)+'%',cls:pct>0?'up':pct<0?'down':'flat'};
}

function chgBadge(chg){
  if(!chg) return '<span class="flat">—</span>';
  return '<span class="'+chg.cls+'">'+chg.pct+'</span>';
}
function toggleTableDrill(row,type,key){
  let md=currentMd(); if(!md)return;
  let id=type==='daily'?key.replace(/[^a-zA-Z0-9]/g,'_'):hashId(key);
  let detailEl=document.getElementById('dd-'+id);
  let innerEl=document.getElementById('ddi-'+id);
  if(!detailEl||!innerEl)return;
  let isOpen=detailEl.classList.contains('show');
  document.querySelectorAll('.drill-detail.show').forEach(function(el){el.classList.remove('show')});
  document.querySelectorAll('.drill-row.expanded').forEach(function(el){el.classList.remove('expanded')});
  if(!isOpen){
    let cacheKey=type+':'+key;
    if(!drillCache[cacheKey])drillCache[cacheKey]=buildTableDrill(type,key,md);
    innerEl.innerHTML=drillCache[cacheKey];
    detailEl.classList.add('show');
    row.classList.add('expanded');
    row.scrollIntoView({behavior:'smooth',block:'nearest'});
  }
}

function buildTableDrill(type,key,md){
  let drillData,sections;
  if(type==='daily'){
    drillData=md.daily_drill&&md.daily_drill[key];
    sections=[{title:'按账号',data:drillData?drillData.accounts:{}},{title:'按产品',data:drillData?drillData.products:{}},{title:'按渠道',data:drillData?drillData.channels:{}}];
  }else if(type==='product'){
    drillData=md.product_drill&&md.product_drill[key];
    sections=[{title:'按账号',data:drillData?drillData.accounts:{}},{title:'按渠道',data:drillData?drillData.channels:{}}];
  }else if(type==='channel'){
    drillData=md.channel_drill&&md.channel_drill[key];
    sections=[{title:'按产品',data:drillData?drillData.products:{}},{title:'按账号',data:drillData?drillData.accounts:{}}];
  }else return'';
  let html='';
  sections.forEach(function(sec){
    let entries=Object.entries(sec.data).sort(function(a,b){return b[1].总订单-a[1].总订单});
    if(entries.length===0){html+='<div class="drill-col"><h4>'+sec.title+'</h4><span style="color:var(--text-muted);font-size:11px">无数据</span></div>';return}
    html+=drillCol(sec.title,entries);
  });
  return html;
}

// ── 排序 ──
function sortTbl(id,col){
  let tb=document.getElementById(id).querySelector('tbody');
  let allRows=Array.from(tb.querySelectorAll('tr'));
  let rows=allRows.filter(function(r){return r.classList.contains('drill-row')});
  let th=document.getElementById(id).querySelectorAll('th')[col];
  let asc=th.classList.contains('sorted-asc');
  document.getElementById(id).querySelectorAll('th').forEach(function(h){h.classList.remove('sorted-asc','sorted-desc')});
  let isDateCol=(id==='tblDaily'&&col===0);
  rows.sort(function(a,b){
    let va=(a.cells[col].textContent||'').replace(/[,%]/g,'').trim();
    let vb=(b.cells[col].textContent||'').replace(/[,%]/g,'').trim();
    if(isDateCol)return asc?va.localeCompare(vb):vb.localeCompare(va);
    let na=parseFloat(va),nb=parseFloat(vb);
    if(!isNaN(na)&&!isNaN(nb))return asc?na-nb:nb-na;
    return asc?vb.localeCompare(va):va.localeCompare(vb,'zh');
  });
  th.classList.add(asc?'sorted-desc':'sorted-asc');
  let tbody=document.getElementById(id).querySelector('tbody');
  let detailMap={};
  allRows.forEach(function(r){if(r.classList.contains('drill-detail')){let prev=r.previousElementSibling;if(prev&&prev.classList.contains('drill-row'))detailMap[rows.indexOf(prev)]=r}});
  tbody.innerHTML='';
  rows.forEach(function(r,i){tbody.appendChild(r);if(detailMap[i])tbody.appendChild(detailMap[i])});
}

function hashId(s){return s.replace(/[^a-zA-Z0-9\\u4e00-\\u9fa5]/g,'_').substring(0,50)}

function onMonthChange(){currentMonth=document.getElementById('monthSelect').value;ovTargetDate=null;ovExpL1=null;ovExpL2=null;renderAll()}
function prevMonth(){let i=MONTHS.indexOf(currentMonth);if(i>0){currentMonth=MONTHS[i-1];document.getElementById('monthSelect').value=currentMonth;ovTargetDate=null;ovExpL1=null;ovExpL2=null;renderAll()}}
function nextMonth(){let i=MONTHS.indexOf(currentMonth);if(i<MONTHS.length-1){currentMonth=MONTHS[i+1];document.getElementById('monthSelect').value=currentMonth;ovTargetDate=null;ovExpL1=null;ovExpL2=null;renderAll()}}
function toggleTheme(){theme=theme==='dark'?'light':'dark';document.body.classList.toggle('light');localStorage.setItem('jz8-theme',theme);document.getElementById('tbtn').textContent=theme==='light'?'☀️':'🌙';renderAll()}
init();
</script>
</body></html>'''

    # 注入数据 JSON
    html = html.replace('__DATA_JSON__', data_json)

    output_path = DATA_DIR / "简知分销数据看板.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"   大小: {output_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
