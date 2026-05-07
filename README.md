<p align="center">
  <img src="docs/assets/676bfb97-434a-404d-b7f9-87432fdf1b67.png" alt="LiMem project overview" width="880">
</p>

<h1 align="center">LiMem</h1>

<p align="center">
  <strong>面向 Agent 的团队级长时记忆系统，让复杂端侧数据流沉淀为可检索、可演化、可审计的记忆图谱。</strong>
</p>

<p align="center">
  <a href="https://github.com/gaooooosh/LiMem"><img alt="Project" src="https://img.shields.io/badge/project-LiMem-111827?style=flat-square"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-service-009688?style=flat-square&logo=fastapi&logoColor=white">
  <img alt="React" src="https://img.shields.io/badge/React-console-61DAFB?style=flat-square&logo=react&logoColor=111827">
  <img alt="Docker" src="https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker&logoColor=white">
  <img alt="Access Control" src="https://img.shields.io/badge/access-control-7C3AED?style=flat-square">
</p>

<p align="center">
  <a href="#快速开始"><strong>快速开始</strong></a>
  ·
  <a href="#http-api服务接口"><strong>HTTP API</strong></a>
  ·
  <a href="#web-控制台"><strong>Web 控制台</strong></a>
  ·
  <a href="docs/architecture.md"><strong>Architecture</strong></a>
  ·
  <a href="docs/development.md"><strong>Development</strong></a>
</p>

LiMem 是一个面向端侧环境设计的 Agent 长时记忆库。它可以接入任意输入数据流，包括对话文本、JSON、设备事件、传感器状态、工具调用记录和业务日志，并将这些观测转化为可持久化、可检索、可持续演化的记忆。

端侧 Agent 面对的不是干净的聊天记录，而是多源、碎片化、强上下文依赖的数据。LiMem 会把这些复杂输入沉淀为情景化记忆，并在后续对话、推理和工具调用中召回与当前场景最相关的部分。

## LiMem Online

<table>
  <tr>
    <td>
      <p><strong>LiMem 既可以作为开源项目自部署，也提供持续运营的托管版本。</strong>你可以从源码开始集成，也可以直接使用在线控制台获得免运维的运行环境。</p>
      <table>
        <tr>
          <td>
            <h3>Open-source Core</h3>
            <p>完整的记忆建模、图谱存储、权限隔离服务、审计日志和控制台源码。适合私有化部署、二次开发和端侧集成。</p>
            <p><a href="#docker-部署"><strong>自部署 LiMem -></strong></a></p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>Managed Service</h3>
            <p>基于开源核心提供在线托管控制台和运营环境。接入凭证、权限范围和服务入口由托管控制台统一管理。</p>
            <p><a href="https://limem.gaooooosh.art/ui/login"><strong>进入 LiMem 托管控制台 -></strong></a></p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>

## 可以做什么

<table>
  <tr>
    <td>
      <p><strong>LiMem 把杂乱的端侧观察转成可长期使用的记忆资产。</strong>核心能力围绕摄入、组织、召回、演化和观测展开。</p>
      <table>
        <tr>
          <td>
            <h3>统一记忆摄入</h3>
            <p>对话、JSON、日志、设备事件、工具调用都可以直接写入，自动沉淀为结构化记忆。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>情景相关召回</h3>
            <p>不是简单关键词匹配，而是围绕当前上下文找到真正相关的历史事件、状态和偏好。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>可追踪记忆图谱</h3>
            <p>保留事件、实体、上下文和关系，让每条记忆都有来源、关联和演化路径。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>持续演化</h3>
            <p>支持上下文复用、事件关系、合并、归档和索引重建，避免长期记忆持续碎片化。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>本地优先部署</h3>
            <p>使用本地 Kuzu 持久化，适合弱网、隐私敏感、边缘设备和私有化环境。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>可视化与审计</h3>
            <p>内置图谱视图、审计日志和 Web 控制台，方便调试召回质量和观察记忆变化。</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>

## 适用场景

<table>
  <tr>
    <td>
      <p><strong>LiMem 适合需要长期上下文、可解释记忆和可控部署的 Agent 产品。</strong></p>
      <table>
        <tr>
          <td>
            <h3>个人 AI 助手</h3>
            <p>长期记住用户偏好、习惯、历史请求和跨会话上下文。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>车载 / IoT Agent</h3>
            <p>融合用户对话、设备状态、传感器变化和业务事件流。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>客服与运营助手</h3>
            <p>跨会话保留用户背景、问题进展、处理记录和后续动作。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>私有化 AI 应用</h3>
            <p>在隐私敏感、内网部署或弱网环境中运行可控的记忆系统。</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>

## 快速开始

<table>
  <tr>
    <td>
      <p><strong>从本地 Python SDK 开始，几分钟内完成一次写入和检索。</strong>服务部署可以继续看后面的 HTTP API、Web 控制台和 Docker 部署章节。</p>
      <table>
        <tr>
          <td>
            <h3>1. 准备环境</h3>
            <p>需要 Python 3.12+、<code>uv</code>，服务部署需要 Docker / Docker Compose。前端开发需要 Node.js 20+。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>2. 安装依赖</h3>
            <pre><code class="language-bash">git clone https://github.com/gaooooosh/LiMem.git
cd LiMem
uv sync
cp .env.example .env</code></pre>
          </td>
        </tr>
        <tr>
          <td>
            <h3>3. 配置密钥</h3>
            <pre><code class="language-bash">DASHSCOPE_API_KEY=your_api_key
ROOT_API_KEY=change-me-to-a-long-random-token</code></pre>
          </td>
        </tr>
      </table>
      <h3>Python SDK 示例</h3>
      <pre><code class="language-python">import time
from limem import create_ltm

ltm = create_ltm(db_path="./DB/demo_db.kz")

result = ltm.ingest_text(
    "用户说：导航去公司，车机回答：已开始导航。",
    timestamp=int(time.time()),
)

print(result.to_dict())
print(ltm.retrieve_memories("用户最近导航去了哪里？", top_k=5))</code></pre>
      <p>运行脚本时设置 <code>PYTHONPATH</code>：</p>
      <pre><code class="language-bash">PYTHONPATH=src uv run python your_script.py</code></pre>
    </td>
  </tr>
</table>

## HTTP API（服务接口）

<table>
  <tr>
    <td>
      <p><strong>HTTP 服务提供用户、Key、数据库、记忆写入/检索和审计接口。</strong>完整接口见 <a href="docs/http-api.md">HTTP API 文档</a>。</p>
      <table>
        <tr>
          <td>
            <h3>启动服务</h3>
            <pre><code class="language-bash">ROOT_API_KEY=change-me-to-a-long-random-token \
PYTHONPATH=src uv run python -m service.main</code></pre>
          </td>
        </tr>
        <tr>
          <td>
            <h3>鉴权方式</h3>
            <p>推荐使用 <code>X-API-Key</code>，也支持 <code>Authorization: Bearer ...</code>。</p>
            <pre><code class="language-bash">export BASE=http://127.0.0.1:8000
export ROOT_KEY=change-me-to-a-long-random-token
export USER_KEY=your-user-api-key</code></pre>
          </td>
        </tr>
        <tr>
          <td>
            <h3>Scope</h3>
            <p><code>r</code> 读取；<code>w</code> 写入、建库、演化和索引维护；<code>admin</code> 管理用户和全局资源。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>管理员初始化</h3>
            <pre><code class="language-bash">curl -sS -X POST "$BASE/admin/users" \
  -H "X-API-Key: $ROOT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name":"alice"}'

curl -sS -X POST "$BASE/admin/users/{user_id}/keys" \
  -H "X-API-Key: $ROOT_KEY" \
  -H "Content-Type: application/json" \
  -d '{"label":"laptop","scopes":"r,w"}'</code></pre>
          </td>
        </tr>
        <tr>
          <td>
            <h3>普通用户流程</h3>
            <pre><code class="language-bash">curl -sS "$BASE/me" -H "X-API-Key: $USER_KEY"
curl -sS "$BASE/me/keys" -H "X-API-Key: $USER_KEY"

curl -sS -X POST "$BASE/databases" \
  -H "X-API-Key: $USER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"display_name":"my-memory"}'</code></pre>
          </td>
        </tr>
        <tr>
          <td>
            <h3>写入记忆</h3>
            <pre><code class="language-bash">curl -sS -X POST "$BASE/db/{db_id}/ingest" \
  -H "X-API-Key: $USER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"data":{"source":"device_event_stream","payload":{"user_intent":"drive_to_office"}}}'</code></pre>
          </td>
        </tr>
        <tr>
          <td>
            <h3>查询与观测</h3>
            <pre><code class="language-bash">curl -sS -X POST "$BASE/db/{db_id}/query" \
  -H "X-API-Key: $USER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"用户最近导航去了哪里","top_k":5}'

curl -sS "$BASE/db/{db_id}/health" -H "X-API-Key: $USER_KEY"
curl -sS "$BASE/db/{db_id}/api/audit/recent?limit=20" -H "X-API-Key: $USER_KEY"</code></pre>
          </td>
        </tr>
      </table>
      <p><strong>常用入口：</strong><code>/me</code> · <code>/me/keys</code> · <code>/databases</code> · <code>/db/{db_id}/ingest</code> · <code>/db/{db_id}/query</code> · <code>/db/{db_id}/health</code> · <code>/ui/login</code></p>
    </td>
  </tr>
</table>

## Web 控制台

<table>
  <tr>
    <td>
      <p><strong>服务内置 React 控制台。</strong>Docker 构建时前端会自动打包并复制到 FastAPI 静态目录，生产环境直接访问 <code>/ui/login</code>。</p>
      <table>
        <tr>
          <td>
            <h3>访问入口</h3>
            <p>本地 Docker 控制台：<code>http://127.0.0.1:8012/ui/login</code></p>
            <p>反向代理后：<code>https://your-domain.example/ui/login</code></p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>用户视图</h3>
            <p>普通用户进入“我的库”，可管理自己的数据库、写入和检索记忆，并自助管理 API Key。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>管理视图</h3>
            <p>管理员进入“管理后台”，可创建用户、签发 Key、查看全局数据库和服务状态。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>本地前端开发</h3>
            <pre><code class="language-bash">cd web
npm install
npm run dev</code></pre>
            <p>Vite dev server 默认运行在 <code>http://127.0.0.1:5173</code>，并把 API 请求代理到 <code>http://127.0.0.1:8000</code>。</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>

## Docker 部署

<table>
  <tr>
    <td>
      <p><strong>Docker Compose 会构建前端、打包后端并挂载本地数据目录。</strong>默认只监听本机，适合配合 Caddy / Nginx / Cloudflare Tunnel 对外提供 HTTPS。</p>
      <table>
        <tr>
          <td>
            <h3>启动</h3>
            <pre><code class="language-bash">docker compose up -d --build</code></pre>
          </td>
        </tr>
        <tr>
          <td>
            <h3>默认访问</h3>
            <p><code>http://127.0.0.1:8012/ui/login</code></p>
            <p><code>http://127.0.0.1:8012</code></p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>数据持久化</h3>
            <p><code>./DB</code>：鉴权 SQLite 与 Kuzu 数据库。</p>
            <p><code>./outputs</code>：审计日志和导出结果。</p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>端口绑定</h3>
            <pre><code class="language-yaml">ports:
  - "127.0.0.1:8012:8000"</code></pre>
          </td>
        </tr>
        <tr>
          <td>
            <h3>生产反向代理</h3>
            <p>将域名转发到 <code>127.0.0.1:8012</code> 即可。反向代理只需要转发 HTTP 请求，鉴权由 LiMem API 自己完成。</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>

## 文档

<table>
  <tr>
    <td>
      <p><strong>更深入的工程细节放在 docs 目录。</strong>README 只保留高频路径，详细接口和开发流程请看对应文档。</p>
      <table>
        <tr>
          <td>
            <h3>Architecture</h3>
            <p>系统分层、用户与工作区模型、写入/检索路径、Web 控制台挂载方式。</p>
            <p><a href="docs/architecture.md"><strong>阅读架构文档 -></strong></a></p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>HTTP API</h3>
            <p>认证、权限、管理员接口、用户自助接口、数据库接口、记忆和图谱 API。</p>
            <p><a href="docs/http-api.md"><strong>查看接口文档 -></strong></a></p>
          </td>
        </tr>
        <tr>
          <td>
            <h3>Development</h3>
            <p>本地开发、前端调试、Docker 工作流、测试命令和仓库维护约定。</p>
            <p><a href="docs/development.md"><strong>查看开发文档 -></strong></a></p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>

## License

<table>
  <tr>
    <td>
      <p>No license file is currently included. Add a <code>LICENSE</code> file before publishing this as an open-source project.</p>
    </td>
  </tr>
</table>
