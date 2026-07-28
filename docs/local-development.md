# 本地开发

后端要求 Python 3.12 或更新的受支持版本以及 PostgreSQL 13+，前端要求 Node.js 22+ 的受支持版本。PostgreSQL 13 的下限来自判题资源回收所用的 `DROP DATABASE ... WITH (FORCE)`；生产或联网环境应使用仍在官方支持期的数据库版本。不要使用已经停止安全维护的 Python 3.9；`requirements.txt` 中的直接依赖版本已固定并经过测试。

如果 `backend/.venv` 已由旧版 Python 创建，先用 `.venv/bin/python --version` 确认。版本低于 3.12 时应先备份或移走旧目录，再新建虚拟环境；不要在旧环境上原地混装新版 Python 依赖。

## 架构边界

V1 是本地优先架构：FastAPI 进程同时提供 API，并用进程内的专用线程池调度判题。它不需要 Redis、Celery、Docker Compose 或远程服务。开发环境建议只运行一个后端进程；`--reload`、进程重启或关机会中断进行中的判题，下一次启动会把遗留的 `PENDING`/`RUNNING` 提交标记为 `SE`，并尝试清理遗留资源。

## PostgreSQL 与环境变量

先复制仓库根目录的环境变量模板：

```bash
cd <仓库目录>
test -e .env || (umask 077 && cp .env.example .env)
chmod 600 .env
```

该命令只在 `.env` 不存在时创建它，不会覆盖已有配置，并将文件权限收紧为仅当前用户可读写。配置从仓库根目录的 `.env` 读取，与启动命令所在目录无关。默认值为：

```dotenv
APP_DATABASE_URL=postgresql+psycopg:///sql_oj?host=/tmp
ADMIN_DATABASE_URL=postgresql+psycopg:///postgres?host=/tmp
JUDGE_RUNNER_ROLE=oj_runner
JWT_SECRET_KEY=
JWT_EXPIRES_MINUTES=10080
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
TRUSTED_HOSTS=localhost,127.0.0.1
```

两个数据库 URL 必须使用 `postgresql+psycopg` 驱动并包含数据库名。默认 URL 通过 `/tmp` 下的 Unix socket，以当前系统用户连接，不依赖某个固定用户名。如果本机 PostgreSQL 使用其他 socket 目录，可以修改 `host`；如果使用 TCP，则写完整的用户名、密码、主机和端口，例如：

```dotenv
APP_DATABASE_URL=postgresql+psycopg://app_user:password@127.0.0.1:5432/sql_oj
ADMIN_DATABASE_URL=postgresql+psycopg://db_admin:password@127.0.0.1:5432/postgres
```

应用、迁移、初始化和判题管理连接统一强制 5 秒建连超时；数据库 URL 中更大的 `connect_timeout` 不会覆盖这个上限。

不要用 URL 查询参数中的 `service`、`servicefile` 或 `options` 间接覆盖连接身份；初始化会拒绝这些设置以及重复的 `user`/`dbname`。进程环境中的 `PGSERVICE`、`PGSERVICEFILE` 和 `PGOPTIONS` 也会被拒绝，请把所需连接参数明确写入数据库 URL。应用库也不能指向 `postgres`、`template0`、`template1` 或判题临时库前缀 `oj_run_`。

`APP_DATABASE_URL` 用于应用数据和 Alembic 迁移。`ADMIN_DATABASE_URL` 用于初始化以及创建、检查、终止和删除每个测试用例的临时角色与临时数据库；其数据库角色必须具备相应权限。两者使用的角色都不能与 `JUDGE_RUNNER_ROLE` 相同。`JUDGE_RUNNER_ROLE` 必须是最多 63 个字符的小写 PostgreSQL 标识符，不能是 `postgres`、不能以 `pg_` 开头，也不能占用 `oj_exec_` 前缀。

如果 `APP_DATABASE_URL` 使用独立角色，该角色必须已经存在、允许登录，并能用 URL 中的认证信息连接；初始化脚本不会替你生成或打印数据库密码。

`oj_run_` 数据库前缀和 `oj_exec_` 角色前缀是判题器的可销毁资源命名空间：应用库、管理员库及其连接角色不得使用这些前缀，也不要在同一 PostgreSQL 集群中把它们用于其他对象。启动恢复只会清理名称、所有者、资源注释和低权限形态都符合判题器规则的遗留对象；旧版共享角色拥有的遗留数据库按受限的旧命名规则兼容清理。

初始化脚本会：

- 创建 `APP_DATABASE_URL` 指定的应用数据库（默认 `sql_oj`），新建时把所有者设为应用角色；复用已有库时不改所有者，只授予并验证 Alembic 所需的连接与 `public` schema 权限；
- 创建或收紧 `JUDGE_RUNNER_ROLE`（默认 `oj_runner`），清除遗留密码和角色级配置，确保它是 `NOLOGIN`、`NOINHERIT` 且无建库、建角色、复制和绕过 RLS 等权限；
- 双向移除该角色的不安全成员关系，并只让管理员连接角色能够管理它；
- 用有超时的 PostgreSQL advisory lock 串行化并发初始化，避免角色或数据库的检查后创建竞争。

运行时不会使用旧式的 `SET ROLE oj_runner` 执行用户 SQL；每个测试用例会另建一次性角色和数据库，详见[判题安全说明](judge-security.md)。

## 认证、Host 与跨域

`JWT_SECRET_KEY` 应设置为随机且不少于 32 字节的值，例如：

```bash
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

把输出复制到 `.env`。如果该值留空或仍是开发占位值，后端会记录警告并为本进程生成临时密钥；服务重启后，所有现有登录令牌都会失效。

`CORS_ORIGINS` 是逗号分隔的完整 `http`/`https` origin（包含非默认端口，不包含路径、查询参数或片段）。不接受 `*`，也不启用跨域凭据 cookie。新增前端地址时必须明确加入列表。

`TRUSTED_HOSTS` 是逗号分隔的 HTTP `Host` 主机名，不写协议、端口或路径。可使用形如 `*.example.test` 的子域通配符，但不能使用裸 `*`。通过其他主机名访问后端时，必须同步加入此项，否则请求会被拒绝。

## 启动后端

```bash
cd <仓库目录>/backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.scripts.setup_db
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

健康检查为 <http://127.0.0.1:8000/api/health>。第一个成功注册的用户成为管理员；之后的注册用户为普通用户。

迁移 `0002` 需要检查并规范目标库中的既有数据，因此不支持 `alembic upgrade --sql` 离线生成；请在备份后直接对目标 PostgreSQL 执行在线 `alembic upgrade head`。`setup_db` 只负责数据库与角色初始化，不会代替 Alembic 迁移；API 启动时会核对当前 Alembic head，未迁移到仓库最新版时会明确拒绝启动。

## 启动前端

```bash
cd <仓库目录>/frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

打开 <http://127.0.0.1:5173>。Vite 开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`。

Vite 开发与预览服务器会发送 CSP `frame-ancestors 'none'`、`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff` 和 `Referrer-Policy: no-referrer`，并把同源 `/api/*` 反代到 `127.0.0.1:8000`。如果把 `dist/` 交给其他静态服务器部署，必须同时：

- 配置等价安全响应头，避免已登录页面被第三方站点嵌入并诱导点击；
- 把 `/api/*` 反代到 FastAPI，不能让 SPA fallback 接管 API 请求；
- 对其他未知 GET 路径回退到 `index.html`，否则直接打开或刷新 `/problems/<slug>` 会得到 404；
- 把前端挂在 origin 根路径。若要部署到 `/sql-judge/` 等子路径，必须一起配置 Vite `base`、Wouter `base` 和 API 前缀。

## 验证改动

后端：

```bash
cd <仓库目录>/backend
source .venv/bin/activate
pytest
```

前端：

```bash
cd <仓库目录>/frontend
npm test
npm run build
```

同时修改前后端时，两组检查都应通过。不要提交 `.env`、`.venv`、`node_modules`、`dist` 或测试缓存。
