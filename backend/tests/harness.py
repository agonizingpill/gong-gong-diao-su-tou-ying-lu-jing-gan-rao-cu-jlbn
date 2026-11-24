"""标准库内存仓储台。

本机没有 pip / docker，无法安装 SQLAlchemy 与 FastAPI。本模块按后端实际用到的
SQLAlchemy 接口子集提供同名替身（Table / Column / select / with_for_update /
in_ / order_by 等），让 domain 下的真实规则代码无需改动即可在内存库中运行。
"""
import sys
import types
import uuid


# ---------------------------------------------------------------- sqlalchemy 替身
class _Column:
    def __init__(self, name, *args, **kwargs):
        self.name = name
        self.table = None
        self.foreign_key = next((a for a in args if isinstance(a, _ForeignKey)), None)
        self.primary_key = kwargs.get('primary_key', False)
        self.nullable = kwargs.get('nullable', True)
        self.default = kwargs.get('default')

    def __eq__(self, other):
        return ('eq', self.name, other)

    def __ne__(self, other):
        return ('ne', self.name, other)

    def __hash__(self):
        return hash(self.name)

    def in_(self, values):
        if isinstance(values, _Select):
            return ('in_select', self.name, values)
        return ('in', self.name, list(values))

    def __getattr__(self, item):
        # 支持 col.c 之类的误写时给出明确错误
        raise AttributeError(item)


def Column(name, *args, **kwargs):
    return _Column(name, *args, **kwargs)


class _ForeignKey:
    def __init__(self, target):
        self.target = target


def ForeignKey(target):
    return _ForeignKey(target)


class _Table:
    def __init__(self, name, metadata, *columns, **kwargs):
        self.name = name
        self.columns = [c for c in columns if isinstance(c, _Column)]
        self._colmap = {c.name: c for c in self.columns}
        for col in self.columns:
            col.table = self
        metadata.tables[name] = self

    @property
    def c(self):
        return _ColumnAccessor(self._colmap)

    def insert(self):
        return _Insert(self)

    def update(self):
        return _Update(self)

    def scalar_subquery(self):
        return self


class _ColumnAccessor:
    def __init__(self, colmap):
        self._colmap = colmap

    def __getattr__(self, item):
        try:
            return self._colmap[item]
        except KeyError:
            raise AttributeError(item)


def Table(name, metadata, *columns, **kwargs):
    return _Table(name, metadata, *columns, **kwargs)


class _MetaData:
    def __init__(self):
        self.tables = {}

    def create_all(self, engine):
        pass


def MetaData():
    return _MetaData()


def create_engine(url, **kwargs):
    return types.SimpleNamespace(url=url)


# ------------------------------------------------------------- 查询构造与执行
class _Select:
    def __init__(self, columns):
        self.columns = columns
        self.filters = []
        self.order = []


def select(*columns):
    flat = []
    for col in columns:
        flat.extend(col if isinstance(col, (list, tuple)) else [col])
    return _Select(flat)


class _Insert:
    def __init__(self, table):
        self.table = table
        self.values_ = None

    def values(self, **data):
        self.values_ = data
        return self


class _Update:
    def __init__(self, table):
        self.table = table
        self.filters = []
        self.values_ = None

    def where(self, *conditions):
        self.filters.extend(conditions)
        return self

    def values(self, **data):
        self.values_ = data
        return self


# _Select.where / order_by 链式方法
def _where(self, *conditions):
    self.filters.extend(conditions)
    return self


def _order_by(self, *columns):
    self.order.extend(columns)
    return self


def _with_for_update(self):
    return self


_Select.where = _where
_Select.order_by = _order_by
_Select.with_for_update = _with_for_update


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter([dict(r) for r in self.rows])

    def first(self):
        return dict(self.rows[0]) if self.rows else None

    def scalar(self):
        if not self.rows:
            return None
        first = self.rows[0]
        return next(iter(first.values()))

    def fetchall(self):
        return [dict(r) for r in self.rows]


class MemoryConn:
    def __init__(self, store):
        self.store = store

    def _resolve_select(self, sub):
        target_col = sub.columns[0]
        rows = self._select_rows(target_col.table, sub)
        return [row[target_col.name] for row in rows]

    def _match(self, row, condition):
        kind = condition[0]
        if kind == 'eq':
            return row.get(condition[1]) == condition[2]
        if kind == 'ne':
            return row.get(condition[1]) != condition[2]
        if kind == 'in':
            return row.get(condition[1]) in condition[2]
        if kind == 'in_select':
            return row.get(condition[1]) in self._resolve_select(condition[2])
        raise AssertionError(f'不支持的条件 {kind}')

    def _select_rows(self, entity, query):
        table = entity if isinstance(entity, _Table) else entity.table
        rows = [dict(r) for r in self.store[table.name]]
        for condition in query.filters:
            rows = [r for r in rows if self._match(r, condition)]
        for col in reversed(query.order):
            rows.sort(key=lambda r, name=col.name: (r[name] is None, r[name]))
        return rows

    def _find_table(self, column):
        if column.table is not None:
            return column.table
        for table in METADATA.tables.values():
            if column.name in table._colmap:
                return table
        raise AssertionError(f'找不到列 {column.name} 对应的表')

    def execute(self, statement):
        if isinstance(statement, _Select):
            entity = statement.columns[0]
            rows = self._select_rows(entity, statement)
            return _Result(rows)
        if isinstance(statement, _Insert):
            row = dict(statement.values_)
            self.store[statement.table.name].append(row)
            return _Result([])
        if isinstance(statement, _Update):
            table = statement.table
            changed = 0
            for row in self.store[table.name]:
                if all(self._match(row, c) for c in statement.filters):
                    row.update(statement.values_)
                    changed += 1
            return _Result([])
        raise AssertionError(f'不支持的语句 {type(statement)}')

    def scalar(self, statement):
        return self.execute(statement).scalar()


METADATA = _MetaData()


# --------------------------------------------------------------- 事务与外部依赖替身
class _Transaction:
    def __init__(self, store):
        self.conn = MemoryConn(store)

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


def make_transaction(store):
    @staticmethod
    def _ctx():
        return _Transaction(store)
    return _ctx


def install_stubs(store):
    """在导入 domain 之前安装全部第三方依赖替身。"""
    sa = types.ModuleType('sqlalchemy')
    for name in ('Boolean', 'CheckConstraint', 'Float', 'Integer', 'JSON', 'String'):
        setattr(sa, name, lambda *a, **k: None)
    sa.Boolean = sa.Float = sa.Integer = sa.JSON = sa.String = lambda *a, **k: None
    sa.CheckConstraint = lambda *a, **k: None
    sa.UniqueConstraint = lambda *a, **k: None
    sa.Column = Column
    sa.ForeignKey = ForeignKey
    sa.Table = Table
    sa.MetaData = MetaData
    sa.create_engine = create_engine
    sa.select = select
    sa.text = lambda sql: sql
    sa.func = types.SimpleNamespace(count=lambda *a: 'count')
    sys.modules['sqlalchemy'] = sa

    fastapi = types.ModuleType('fastapi')

    class HTTPException(Exception):
        def __init__(self, status_code=400, detail=''):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class APIRouter:
        def __init__(self, *a, **k):
            pass

        def get(self, *a, **k):
            return lambda fn: fn

        def post(self, *a, **k):
            return lambda fn: fn

    class _Depends:
        def __call__(self, fn=None):
            return fn

    fastapi.HTTPException = HTTPException
    fastapi.APIRouter = APIRouter
    fastapi.Depends = _Depends()
    sys.modules['fastapi'] = fastapi

    security = types.ModuleType('fastapi.security')

    class HTTPBearer:
        def __init__(self, *a, **k):
            pass

    security.HTTPAuthorizationCredentials = object
    security.HTTPBearer = HTTPBearer
    sys.modules['fastapi.security'] = security

    pydantic = types.ModuleType('pydantic')

    class BaseModel:
        def model_dump(self):
            return dict(self.__dict__)

    class _ConfigDict(dict):
        pass

    def Annotated_shim(*args, **kwargs):
        return args[0] if args else None

    class _Constraints:
        def __init__(self, *a, **k):
            pass

    pydantic.BaseModel = BaseModel
    pydantic.ConfigDict = _ConfigDict
    pydantic.FiniteFloat = float
    pydantic.StringConstraints = _Constraints
    pydantic.Annotated = Annotated_shim
    sys.modules['pydantic'] = pydantic

    redis_mod = types.ModuleType('redis')

    class Redis:  # 测试不走真实队列
        @classmethod
        def from_url(cls, *a, **k):
            return cls()

        def ping(self):
            return True

    redis_mod.Redis = Redis
    sys.modules['redis'] = redis_mod

    # core.config 需要环境变量
    import os
    os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
    os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')
    os.environ.setdefault('JWT_SECRET', 'test-secret')

    # core.db 绑定本测试台的 metadata / transaction
    import core.db as db
    db.metadata = METADATA
    db.engine = create_engine('sqlite:///:memory:')
    db.transaction = make_transaction(store)

    # 计算任务替身：测试直接预置 ready 结果，pin_map 经 queueing.result 取回
    import core.queueing as queueing
    jobs = {}

    def fake_submit(user, payload):
        token = uuid.uuid4().hex
        jobs[token] = {'owner': user['sub'], 'input': payload}
        return {'token': token, 'status': 'queued'}

    def fake_result(user, token):
        job = jobs[token]
        return job  # 预置为 {'input':..., 'result':...}

    queueing.submit = fake_submit
    queueing.result = fake_result
    queueing.jobs = jobs
    return queueing


def seed_store(store, fixtures):
    for table, rows in fixtures:
        store.setdefault(table.name, [])
        for row in rows:
            store[table.name].append(dict(row))
