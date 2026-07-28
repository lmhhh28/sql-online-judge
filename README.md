# SQL Online Judge

一个本地优先的 SQL 练习与判题系统，后端使用 FastAPI、SQLAlchemy 和 PostgreSQL，前端使用 React、TypeScript 与 Vite。

V1 的 API 与判题调度运行在同一个 FastAPI 进程中，不依赖 Redis、Celery、Docker Compose 或远程服务。它适合个人、本机或受信任的小范围环境；如果要接收不受信任用户的 SQL，请先阅读[判题安全说明](docs/judge-security.md)。

## 快速开始

需要 Python 3.12 或更新的受支持版本、PostgreSQL 13+ 和 Node.js 22+ 的受支持版本。Python 3.9 已停止安全维护，不应再用于安装后端依赖；生产或联网环境也应使用仍在官方支持期的 PostgreSQL 与 Node.js 版本。

### 1. 后端

```bash
cd <仓库目录>/backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
test -e ../.env || (umask 077 && cp ../.env.example ../.env)
chmod 600 ../.env
python -m app.scripts.setup_db
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

默认数据库 URL 通过 `/tmp` 下的 PostgreSQL Unix socket 连接，并使用当前系统用户：

```dotenv
APP_DATABASE_URL=postgresql+psycopg:///sql_oj?host=/tmp
ADMIN_DATABASE_URL=postgresql+psycopg:///postgres?host=/tmp
```

如果 PostgreSQL 的 socket 不在 `/tmp`，或需要 TCP、独立用户名和密码，请在仓库根目录的 `.env` 中覆盖这两个值。`ADMIN_DATABASE_URL` 对应的角色必须能创建和删除临时数据库与临时角色。

请为持久登录令牌设置随机且不少于 32 字节的 `JWT_SECRET_KEY`。留空时后端会为当前进程生成临时密钥，重启后已有令牌全部失效。

### 2. 前端

```bash
cd <仓库目录>/frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

打开 <http://127.0.0.1:5173>。第一个成功注册的用户会成为管理员。

## 示例题目

注册首个管理员后，可在管理页面导入 `examples/` 下任意 ZIP 包。上传接口接收 ZIP 原始字节，使用 `Content-Type: application/zip`，不是 `multipart/form-data`。完整约定见[题目包格式](docs/problem-package.md)。

## 更多文档

- [本地开发](docs/local-development.md)
- [题目包格式](docs/problem-package.md)
- [判题安全说明](docs/judge-security.md)
