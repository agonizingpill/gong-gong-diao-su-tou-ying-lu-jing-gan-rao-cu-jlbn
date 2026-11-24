"""补测后保护位移交裁定规则测试。

运行： python -I -m tests.test_handoff_rulings  （在 backend 目录下）

覆盖：
1. 补测不再作废旧待二签移交单，只抬升观测版本；
2. 新版本首版图谱：簇内格子完全相同（簇编号变了也算相同）→ 沿用首签；
3. 少一格 / 多一格 → 退回领班；
4. 二签与归档同时提交：裁定图谱生效前不能转走灯组责任；
5. 归档重试不重复裁定；同版另归档一张图不翻案；
6. 移交单留存首签图谱与裁定图谱，可回查；
7. 非保护观察格仍即签即交。
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.harness import install_stubs, seed_store, METADATA

OPERATOR = {'sub': 'operator@demo.test', 'role': 'operator'}
REVIEWER = {'sub': 'reviewer@demo.test', 'role': 'reviewer'}

store = {}
queueing = install_stubs(store)

from domain.demo import FIXTURES  # noqa: E402  （导入即建表）
from domain.light_handoffs_model import light_handoffs  # noqa: E402
from domain.shadow_plans_model import shadow_plans  # noqa: E402
from domain.sight_cells_model import sight_cells  # noqa: E402
from domain.sculpture_bands_model import sculpture_bands  # noqa: E402
from domain import plot_checks as rules  # noqa: E402
from domain import shadow_plans_service as plan_service  # noqa: E402
from core.records import get  # noqa: E402
from pydantic import BaseModel  # noqa: E402


class Form(BaseModel):
    def __init__(self, **data):
        self.__dict__.update(data)


# 服务模块在导入时已绑定 queueing 的函数名，同步指向测试替身
plan_service.result = queueing.result
plan_service.submit = queueing.submit

seed_store(store, FIXTURES)

PASS, FAIL = 0, 0


def check(name, condition, detail=''):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f'  ✓ {name}')
    else:
        FAIL += 1
        print(f'  ✗ {name} {detail}')


def expect_conflict(name, fn, fragment):
    try:
        fn()
    except Exception as exc:
        check(name, fragment in str(getattr(exc, 'detail', exc)), f'期望含 {fragment!r}，实际 {exc!r}')
        return
    check(name, False, '应当被拒绝')


def layout(cells, cluster_of):
    return [{'code': code, 'x': int(code.split('-')[1]), 'y': int(code.split('-')[2]),
             'shade': 0.5, 'spill': 0.5, 'protected': False, 'cluster': cluster_of[code]}
            for code in cells]


def grid_cells(cols, rows, band='BANK-01'):
    return [f'{band}-{x}-{y}' for y in range(rows) for x in range(cols)]


def evidence(plots):
    return {'plots': plots, 'grid': [6, 4], 'edges': plots, 'cut_edges': 0,
            'cut_weight_share': 0.0, 'seed': 1096, 'input_hash': 'h'}


def pin(conn, code, band_code, revision, lay):
    """模拟 segment → worker 算完 → pin_map 归档的完整链路。"""
    band = get(conn, sculpture_bands, band_code)
    token = _stage_job(band, lay)
    data = types.SimpleNamespace(code=code, token=token)
    return plan_service.pin_map(conn, REVIEWER, data)


def _stage_job(band, lay):
    token = f'tok-{len(queueing.jobs)}'
    queueing.jobs[token] = {
        'owner': REVIEWER['sub'],
        'input': {'band': dict(band), 'points': [], 'clusters': 2},
        'result': {'algorithm': 'SpectralClustering', 'clusters': 2,
                   'layout': lay, 'evidence': evidence(len(lay))},
    }
    return token


def remeasure(conn, x, y, shade=0.9, spill=0.11):
    rules.record_beam(conn, Form(
        band='BANK-01', x=x, y=y, shade=shade, spill=spill))


print('1. 基线：首版图谱 + 保护位移交单')
with __import__('core.db', fromlist=['transaction']).transaction() as conn:
    cells = grid_cells(6, 4)
    # 保护位 BANK-01-0-0 与 BANK-01-1-0 同簇（簇 1）
    base_clusters = {code: 1 if code in ('BANK-01-0-0', 'BANK-01-1-0') else 2 for code in cells}
    plan1 = pin(conn, 'MAP-1', 'BANK-01', 0, layout(cells, base_clusters))
    check('首版图谱归档于版本 0', plan1['revision'] == 0)
    check('无待裁定时返回空裁定', plan1['rulings'] == [])

    draft = rules.offer_station(conn, OPERATOR, Form(
        code='LX-100', plan='MAP-1', cell='BANK-01-0-0', target='SOUTH'))
    check('保护位移交单进入待二签', draft['status'] == '待二签')
    check('记录首签所用图谱', draft['first_plan'] == 'MAP-1')
    check('尚无裁定图谱', draft['ruling_plan'] is None and draft['ruling_reason'] is None)
    owner_before = get(conn, sight_cells, 'BANK-01-0-0')['owner']
    check('待二签期间责任不转移', owner_before == 'NORTH')

print('2. 补测后移交单不作废，仅抬升观测版本')
with __import__('core.db', fromlist=['transaction']).transaction() as conn:
    remeasure(conn, 2, 0)
    band = get(conn, sculpture_bands, 'BANK-01')
    draft = get(conn, light_handoffs, 'LX-100')
    check('观测版本升到 1', band['revision'] == 1)
    check('待二签移交单仍然保留（不再标记版本失效）', draft['status'] == '待二签')
    check('责任灯组仍未转走', get(conn, sight_cells, 'BANK-01-0-0')['owner'] == 'NORTH')

print('3. 新版本首版图谱：簇内格子完全相同 → 沿用首签（簇编号变化照常）')
with __import__('core.db', fromlist=['transaction']).transaction() as conn:
    # 同样两个格子同簇，但簇编号由 1 改为 3
    same_clusters = {code: 3 if code in ('BANK-01-0-0', 'BANK-01-1-0') else 4 for code in cells}
    plan2 = pin(conn, 'MAP-2', 'BANK-01', 1, layout(cells, same_clusters))
    check('归档即输出裁定', len(plan2['rulings']) == 1 and plan2['rulings'][0]['ruling'] == rules.RULING_KEEP)
    draft = get(conn, light_handoffs, 'LX-100')
    check('裁定结果为沿用首签', draft['ruling_reason'] == rules.RULING_KEEP)
    check('记录裁定所用图谱', draft['ruling_plan'] == 'MAP-2')
    check('移交单仍为待二签，等复核员签收', draft['status'] == '待二签')
    check('签收前责任仍不转移', get(conn, sight_cells, 'BANK-01-0-0')['owner'] == 'NORTH')

    # 复核员签收：补测场景下须有“沿用首签”裁定才可转走责任
    done = rules.check_station(conn, REVIEWER, 'LX-100')
    check('二签完成状态为已移交', done['status'] == '已移交' and done['second_sign'] == REVIEWER['sub'])
    check('二签后责任转到接收灯组', get(conn, sight_cells, 'BANK-01-0-0')['owner'] == 'SOUTH')

print('4. 归档与二签同时提交：裁定图谱生效前不能转走灯组责任')
with __import__('core.db', fromlist=['transaction']).transaction() as conn:
    # 新版本 1 上再签一张待二签单，然后补测升到版本 2，先不归档
    draft2 = rules.offer_station(conn, OPERATOR, Form(
        code='LX-200', plan='MAP-2', cell='BANK-01-4-3', target='NORTH'))
    check('第二张移交单待二签', draft2['status'] == '待二签' and draft2['first_plan'] == 'MAP-2')
    remeasure(conn, 3, 3, shade=0.31, spill=0.4)
    check('版本升到 2', get(conn, sculpture_bands, 'BANK-01')['revision'] == 2)
    expect_conflict('无裁定图谱时二签被拦下，责任不转移',
                    lambda: rules.check_station(conn, REVIEWER, 'LX-200'),
                    '裁定沿用首签后才能签收')
    check('拦单后责任未变', get(conn, sight_cells, 'BANK-01-4-3')['owner'] == 'SOUTH')

print('5. 簇内少一格 → 退回领班')
with __import__('core.db', fromlist=['transaction']).transaction() as conn:
    # 旧图 MAP-2 中 BANK-01-4-3 所在簇的成员；新图把该簇拆小一格
    old_members = rules.cluster_cells(get(conn, shadow_plans, 'MAP-2'), 'BANK-01-4-3')
    smaller = {code: (5 if code in old_members else 6) for code in cells}
    # 让 BANK-01-5-3 离开原簇 → 少一格
    smaller['BANK-01-5-3'] = 7
    plan3 = pin(conn, 'MAP-3', 'BANK-01', 2, layout(cells, smaller))
    ruling = next(r for r in plan3['rulings'] if r['code'] == 'LX-200')
    check('少一格裁定退回领班', ruling['ruling'] == rules.RULING_RETURN)
    draft2 = get(conn, light_handoffs, 'LX-200')
    check('移交单状态改为已退回', draft2['status'] == '已退回')
    check('退回仍记录裁定图谱与原因',
          draft2['ruling_plan'] == 'MAP-3' and draft2['ruling_reason'] == rules.RULING_RETURN)
    check('退回不转走责任', get(conn, sight_cells, 'BANK-01-4-3')['owner'] == 'SOUTH')
    expect_conflict('已退回单不能再二签',
                    lambda: rules.check_station(conn, REVIEWER, 'LX-200'), '仅待二签')

print('6. 簇内多一格 → 同样退回领班')
with __import__('core.db', fromlist=['transaction']).transaction() as conn:
    # 重新签发 LX-300 于 MAP-3，补测升版本 3 后归档 MAP-4，让该簇多并入一格
    rules.offer_station(conn, OPERATOR, Form(
        code='LX-300', plan='MAP-3', cell='BANK-01-4-3', target='NORTH'))
    remeasure(conn, 0, 1, shade=0.41, spill=0.28)
    old_members = rules.cluster_cells(get(conn, shadow_plans, 'MAP-3'), 'BANK-01-4-3')
    bigger = {code: (8 if code in old_members else 9) for code in cells}
    bigger['BANK-01-5-3'] = 8  # MAP-3 拆出的格子重新并入 → 多一格
    plan4 = pin(conn, 'MAP-4', 'BANK-01', 3, layout(cells, bigger))
    ruling = next(r for r in plan4['rulings'] if r['code'] == 'LX-300')
    check('多一格裁定退回领班', ruling['ruling'] == rules.RULING_RETURN)
    check('状态为已退回', get(conn, light_handoffs, 'LX-300')['status'] == '已退回')

print('7. 归档重试不重复裁定；同版另归档一张图不翻案')
with __import__('core.db', fromlist=['transaction']).transaction() as conn:
    # LX-100 在 MAP-2 已获“沿用首签”并完成二签；同版本再归档一张图
    same_revision = {code: (10 if code in ('BANK-01-0-0', 'BANK-01-1-0', 'BANK-01-2-0') else 11)
                     for code in cells}
    plan_extra = pin(conn, 'MAP-2B', 'BANK-01', 1, layout(cells, same_revision))
    check('同版另图不产生新裁定', plan_extra['rulings'] == [])
    done = get(conn, light_handoffs, 'LX-100')
    check('已完成移交不翻案', done['status'] == '已移交' and done['ruling_plan'] == 'MAP-2')

    # 版本 3 再归档一张图：LX-300 已被 MAP-4 裁定退回，不得翻案
    another = {code: (12 if code in ('BANK-01-4-3', 'BANK-01-5-3') else 13) for code in cells}
    plan_retry = pin(conn, 'MAP-4B', 'BANK-01', 3, layout(cells, another))
    check('归档重试不重复裁定', plan_retry['rulings'] == [])
    check('退回结论保持不变', get(conn, light_handoffs, 'LX-300')['ruling_plan'] == 'MAP-4')

print('8. 图谱回查字段齐全；非保护观察格仍即签即交')
with __import__('core.db', fromlist=['transaction']).transaction() as conn:
    row = get(conn, light_handoffs, 'LX-100')
    check('首签图谱可回查', row['first_plan'] == 'MAP-1')
    check('裁定图谱可回查', row['ruling_plan'] == 'MAP-2')
    plain = rules.offer_station(conn, OPERATOR, Form(
        code='LX-900', plan='MAP-4B', cell='BANK-01-1-0', target='SOUTH'))
    check('普通观察格直接已移交', plain['status'] == '已移交')
    check('普通单同样留存首签图谱', plain['first_plan'] == 'MAP-4B' and plain['ruling_reason'] is None)

print('9. 无补测的正常二签、观察位不在新图上、已裁定单不翻案')
with __import__('core.db', fromlist=['transaction']).transaction() as conn:
    # 9a 当前版本上签发并直接二签：无需裁定图谱即可转走责任（0-0 经 LX-100 已转 SOUTH）
    handoff = rules.offer_station(conn, OPERATOR, Form(
        code='LX-910', plan='MAP-4B', cell='BANK-01-0-0', target='NORTH'))
    check('新单待二签', handoff['status'] == '待二签' and handoff['ruling_plan'] is None)
    done = rules.check_station(conn, REVIEWER, 'LX-910')
    check('同版本二签直接完成', done['status'] == '已移交')
    check('同版本二签转走责任', get(conn, sight_cells, 'BANK-01-0-0')['owner'] == 'NORTH')

    # 9b 待签单补测后，新图去掉观察位所在格 → 按“少格”退回领班
    rules.offer_station(conn, OPERATOR, Form(
        code='LX-920', plan='MAP-4B', cell='BANK-01-4-3', target='NORTH'))
    remeasure(conn, 1, 2, shade=0.61, spill=0.22)
    rev4 = get(conn, sculpture_bands, 'BANK-01')['revision']
    kept_pair = ('BANK-01-0-0', 'BANK-01-1-0')
    missing = layout([c for c in cells if c != 'BANK-01-4-3'],
                     {c: (14 if c in kept_pair else 15) for c in cells if c != 'BANK-01-4-3'})
    plan5 = pin(conn, 'MAP-5', 'BANK-01', rev4, missing)
    ruling = next(r for r in plan5['rulings'] if r['code'] == 'LX-920')
    check('观察位不在新图上裁定退回', ruling['ruling'] == rules.RULING_RETURN)
    check('缺失格移交单已退回', get(conn, light_handoffs, 'LX-920')['status'] == '已退回')

    # 9c 已获“沿用首签”但尚未签收的单，后续版本再归档不重复裁定、不翻案
    rules.offer_station(conn, OPERATOR, Form(
        code='LX-930', plan='MAP-5', cell='BANK-01-0-0', target='WEST'))
    remeasure(conn, 2, 2, shade=0.63, spill=0.24)
    rev5 = get(conn, sculpture_bands, 'BANK-01')['revision']
    kept = {code: (15 if code in ('BANK-01-0-0', 'BANK-01-1-0') else 16) for code in cells}
    plan6 = pin(conn, 'MAP-6', 'BANK-01', rev5, layout(cells, kept))
    ruling = next(r for r in plan6['rulings'] if r['code'] == 'LX-930')
    check('首版新图裁定沿用首签', ruling['ruling'] == rules.RULING_KEEP)
    remeasure(conn, 3, 2, shade=0.65, spill=0.26)
    rev6 = get(conn, sculpture_bands, 'BANK-01')['revision']
    changed = {code: (17 if code == 'BANK-01-0-0' else 18) for code in cells}
    plan7 = pin(conn, 'MAP-7', 'BANK-01', rev6, layout(cells, changed))
    check('后续版本不重复裁定', all(r['code'] != 'LX-930' for r in plan7['rulings']))
    row = get(conn, light_handoffs, 'LX-930')
    check('沿用首签结论不翻案', row['ruling_reason'] == rules.RULING_KEEP and row['ruling_plan'] == 'MAP-6')
    check('仍可由复核员签收', rules.check_station(conn, REVIEWER, 'LX-930')['status'] == '已移交')

print(f'\n结果：{PASS} 通过，{FAIL} 失败')
sys.exit(1 if FAIL else 0)
