# 部署

## Docker Compose

安装支持 `docker compose up --wait` 的 Docker Compose v2，准备可用的 LLM 服务和 DashScope 凭据。LLM 默认使用 DeepSeek，也可按[接入文档](llm-providers.md)配置 Ollama、SGLang 或 vLLM。Python 3.12 已包含在镜像中。

```bash
cp .env.example .env
chmod 600 .env
# 编辑 .env，填写管理员、embedding 和所选 LLM 的 key
mkdir -p var
docker compose config --quiet
docker compose up -d --build --wait
curl --fail http://127.0.0.1:8012/health
docker compose ps
```

默认服务地址为 `http://127.0.0.1:8012`。`.env` 和 `config/` 只读挂载，`var/` 可写挂载。缺少 `.env` 时 Compose 直接失败，不创建同名目录。镜像构建使用 uv 0.10.4、`uv.lock` 和依赖哈希，不复制 `.env` 或数据库。Dockerfile 当前用清华 PyPI 镜像安装 uv；锁文件中包下载地址也必须可达。

端口被占用时修改 `.env` 中的 `LIMEM_PORT`。默认仅绑定本机；远程访问需配置自己的反向代理，或明确修改 `LIMEM_BIND_HOST`。本仓库不绑定既有域名或证书。

## 本地 Python

Python 3.12 和 uv 已安装时，在仓库根目录执行：

```bash
cp .env.example .env
# 编辑管理员、embedding 和所选 LLM 的 key
uv sync --frozen --no-dev
HOST=127.0.0.1 PORT=8012 PYTHONPATH=src uv run --frozen --no-dev python -m limem.service.main
```

使用源目录运行以保证配置与静态文档路径一致；当前没有独立 wheel 安装后的配置目录发现机制。不要增加 Uvicorn worker，或启动多个进程共享同一 `var/`：记忆库锁及异步巩固运行状态均在进程内。

## 日常操作

```bash
docker compose logs --tail=100 limem
docker compose restart limem
docker compose down
```

更新源码后执行 `git pull --ff-only` 和 `docker compose up -d --build --wait`。只修改 TOML 或凭据时重启即可；修改端口映射时执行 `docker compose up -d --wait` 重新创建容器。

`var/` 是持久数据。`docker compose down` 保留主机目录。备份前停止服务，完整复制 `var/`，再启动；恢复时配套保留 `.env` 管理员配置及原有用户 key。容器默认以 root 运行，主机备份可能需要相应文件权限。不要仅复制正在写入的单个 SQLite 文件。仓库不包含旧数据库或自动数据迁移机制。

## 验证范围与失败定位

`/health` 验证服务进程。`/docs`、`/openapi.json`、`/guide` 验证接口文档可用。签发用户 key、创建库及格式校验不需要调用模型。完整模型链路需用符合协议的 Session 写入，再执行检索；上游鉴权或模型错误会明确失败。

启动失败先查看 Compose 日志，核对 `.env`、TOML、目录权限和端口。业务错误按[错误文档](service/errors.md)定位。重启会丢失进程内异步巩固状态；已完成写事务仍保存在 SQLite。
