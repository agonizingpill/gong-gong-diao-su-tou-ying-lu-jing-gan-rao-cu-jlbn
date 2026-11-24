from sqlalchemy import select

from core.records import get, insert, need, update
from domain.light_handoffs_model import light_handoffs
from domain.sculpture_bands_model import sculpture_bands
from domain.shadow_plans_model import shadow_plans
from domain.sight_cells_model import sight_cells

FIGURATIVE = '虚构人物雕塑'
SCOPES = (FIGURATIVE, '抽象几何雕塑')
TEAMS = ('NORTH', 'SOUTH', 'WEST')

# 补测后的裁定结果；待二签移交单不再随补测作废，而是等新观测版本的首版图谱裁定
RULING_KEEP = '沿用首签'
RULING_RETURN = '退回领班'
PENDING = '待二签'
TRANSFERRED = '已移交'
RETURNED = '已退回'


def observations(conn, band):
    query = select(sight_cells).where(sight_cells.c.band == band).order_by(sight_cells.c.y, sight_cells.c.x)
    return [dict(row) for row in conn.execute(query).mappings()]


def cluster_cells(plan, cell_code):
    """图谱中与观察位同簇的全部格子编号集合；观察位不在图上时返回 None。"""
    plotted = next((point for point in plan['layout'] if point['code'] == cell_code), None)
    if plotted is None:
        return None
    return {point['code'] for point in plan['layout'] if point['cluster'] == plotted['cluster']}


def pending_handoffs(conn, band_code):
    query = select(light_handoffs).where(
        light_handoffs.c.plan.in_(select(shadow_plans.c.code).where(shadow_plans.c.band == band_code)),
        light_handoffs.c.status == PENDING,
    ).order_by(light_handoffs.c.code)
    return [dict(row) for row in conn.execute(query).mappings()]


def adjudicate_pending(conn, plan):
    """新观测版本首版图谱归档时，对同投影带的待二签移交单一次性裁定。

    新旧图谱中观察位所在簇的格子集合完全相同则沿用首签（簇编号变化不影响），
    少一格或多一格均退回领班。每张移交单只裁定一次，重复归档与同版另图均不翻案。
    """
    rulings = []
    for draft in pending_handoffs(conn, plan['band']):
        if draft['ruling_plan'] is not None:
            continue  # 已裁定过的移交单不重复裁定，同版另图或后续版本均不翻案
        first_plan = get(conn, shadow_plans, draft['first_plan'])
        if first_plan['revision'] >= plan['revision']:
            continue  # 仅等待更新观测版本图谱的移交单才参与裁定
        old_cells = cluster_cells(first_plan, draft['cell'])
        new_cells = cluster_cells(plan, draft['cell'])
        if old_cells is not None and new_cells is not None and old_cells == new_cells:
            reason = RULING_KEEP
            update(conn, light_handoffs, draft['code'], ruling_plan=plan['code'], ruling_reason=reason)
        else:
            reason = RULING_RETURN
            update(conn, light_handoffs, draft['code'], status=RETURNED,
                   ruling_plan=plan['code'], ruling_reason=reason)
        rulings.append({'code': draft['code'], 'ruling': reason, 'plan': plan['code']})
    return rulings


def bump_revision(conn, band):
    # 补测只抬升观测版本：待二签移交单一律保留，等该版本首次归档的图谱裁定
    update(conn, sculpture_bands, band['code'], revision=band['revision'] + 1)


def record_beam(conn, data):
    band = get(conn, sculpture_bands, data.band, lock=True)
    need(0 <= data.x < band['cols'] and 0 <= data.y < band['rows'], '观测格超出网格边界')
    need(0 <= data.shade <= 1 and 0 <= data.spill <= 1, '面积比须在 0 到 1')
    code = f'{band["code"]}-{data.x}-{data.y}'
    query = select(sight_cells).where(sight_cells.c.code == code)
    existing = conn.execute(query).mappings().first()
    if existing is not None and (data.shade, data.spill) == (existing['shade'], existing['spill']):
        return dict(existing)
    if existing is None:
        result = insert(conn, sight_cells, code=code, **data.model_dump(),
                        owner='NORTH' if data.x < band['cols']/2 else 'SOUTH', protected=False)
    else:
        result = update(conn, sight_cells, code, shade=data.shade, spill=data.spill)
    bump_revision(conn, band)
    return result


def protect_station(conn, code):
    cell = get(conn, sight_cells, code)
    band = get(conn, sculpture_bands, cell['band'], lock=True)
    need(band['scope'] == FIGURATIVE, '仅虚构人物雕塑可登记细部保护观察位')
    cell = get(conn, sight_cells, code)
    if cell['protected']:
        return cell
    result = update(conn, sight_cells, code, protected=True)
    bump_revision(conn, band)
    return result


def offer_station(conn, user, data):
    plan = get(conn, shadow_plans, data.plan)
    band = get(conn, sculpture_bands, plan['band'], lock=True)
    need(plan['revision'] == band['revision'], '图谱版本失效，归档当前观测图谱后再移交')
    cell = get(conn, sight_cells, data.cell, lock=True)
    plotted = next((point for point in plan['layout'] if point['code'] == cell['code']), None)
    need(cell['band'] == band['code'] and plotted is not None, '观察位不在当前图谱')
    need(data.target in TEAMS and data.target != cell['owner'], '选择与现任不同的在岗灯组')
    pending = select(light_handoffs.c.code).where(light_handoffs.c.cell == cell['code'],
                                                  light_handoffs.c.status == PENDING)
    need(conn.execute(pending).scalar() is None, '该观察位已有待二签移交单')
    draft = insert(conn, light_handoffs, **data.model_dump(), cluster=plotted['cluster'], source=cell['owner'],
                   status=PENDING if cell['protected'] else TRANSFERRED, first_sign=user['sub'],
                   second_sign=None, first_plan=plan['code'], ruling_plan=None, ruling_reason=None)
    if not cell['protected']:
        update(conn, sight_cells, cell['code'], owner=data.target)
    return draft


def check_station(conn, user, code):
    draft = get(conn, light_handoffs, code)
    plan = get(conn, shadow_plans, draft['plan'])
    band = get(conn, sculpture_bands, plan['band'], lock=True)
    draft = get(conn, light_handoffs, code, lock=True)
    need(draft['status'] == PENDING, '仅待二签移交单可由复核员签收')
    cell = get(conn, sight_cells, draft['cell'], lock=True)
    need(cell['protected'] and cell['owner'] == draft['source'], '观察位责任状态发生变化')
    need(draft['first_sign'] != user['sub'], '保护位二签必须来自另一身份')
    if band['revision'] != plan['revision']:
        # 补测后归档与二签同时提交：裁定图谱尚未生效前，二签不能转走灯组责任
        need(draft['ruling_plan'] is not None,
             '补测后须先归档新观测版本图谱，裁定沿用首签后才能签收')
        need(draft['ruling_reason'] == RULING_KEEP,
             '新图谱簇内格子已变化，该移交单已退回领班，请重新签发')
    update(conn, sight_cells, cell['code'], owner=draft['target'])
    return update(conn, light_handoffs, code, status=TRANSFERRED, second_sign=user['sub'])
