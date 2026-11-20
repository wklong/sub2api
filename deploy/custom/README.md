# 自有镜像部署

本目录维护 wklong/sub2api 的部署入口。应用从自己的源码构建，镜像地址为
`ghcr.io/wklong/sub2api`，当前发布架构为 `linux/amd64`。
本地构建及自有 Compose 默认从 DaoCloud 国内镜像源拉取 Node、Go、Alpine、PostgreSQL
和 Redis 基础镜像；可在构建参数或 `.env` 中覆盖镜像地址。
Compose 通过 `extends` 复用 `../docker-compose.yml` 的应用、PostgreSQL、Redis、
健康检查和环境变量配置，只覆盖镜像、实例名称和必填密钥。

## 一、发布自己的镜像

### main 自动构建

推送到 main 后，GitHub Actions 自动构建并上传 ghcr.io/wklong/sub2api，
提供 main 滚动标签和 sha-<完整提交哈希> 固定标签。推送 test 分支不会发布镜像。
手动运行默认只验证构建；仅在 main 分支勾选 publish_main 时重新发布 main 镜像。等待 Actions 成功后，在服务器 deploy/custom/.env 中
设置 SUB2API_VERSION=main，然后执行：

~~~sh
sh deploy/custom/deploy.sh check
sh deploy/custom/deploy.sh up
sh deploy/custom/deploy.sh status
~~~

每次 main 构建成功后重新执行 up，脚本先拉取镜像，再以 --no-build 重建容器
并等待健康检查。服务器无需源码编译，只需保留基础 Compose、deploy/custom
及 .env 的相对目录结构。生产建议使用 sha-<完整提交哈希> 或正式版本标签，
方便定位和恢复版本；main 会持续变化。构建或上传失败时不要更新服务器。

### 正式版本标签发布

1. 将这些配置通过 PR 合并到自己的默认分支，并确认现有 CI 通过。
2. 在 GitHub 仓库 Actions 页面启用工作流。手动运行 `Custom Image Release`
   默认只验证当前所选分支的构建；仅 main 分支勾选 publish_main 时允许补发镜像。
3. 为已合并的提交创建自己的版本标签。下面是示例，发布前替换版本号：

```sh
git switch main
git pull --ff-only origin main
git tag -a v0.2.23-custom.1 -m "自有版本首次发布"
git push origin v0.2.23-custom.1
```

标签必须符合 `vX.Y.Z-custom.N`，其中 N 是从 1 开始的修订号。
发布流程会检查标签提交属于默认分支，并构建完整前后端镜像。
成功后上传两个标签：完整版本标签，以及 `sha-<完整提交哈希>`。
main 构建更新 `main`；正式版本发布不更新 `main`，不自动更新 `latest`，不创建 GitHub Release 或上传独立二进制。
已发布标签不要移动或复用，修复后递增自有修订号。

GHCR 上传使用工作流内置的 `GITHUB_TOKEN`，无需添加个人令牌。
仓库或组织策略仍须允许 `packages: write`。首次发布后检查 GitHub Packages
的包可见性；公开仓库不代表镜像包一定已公开。
若已有同名包且发布提示无权限，检查包关联仓库及 Actions 访问权限。

原 `Release` 工作流只允许在 `Wei-Shaw/sub2api` 运行；本 Fork 的镜像发布
由 `custom-release.yml` 负责。同步上游后需复查此限制。

## 二、在 Linux x86_64 服务器部署

需要 Docker Engine 和支持 `up --wait` 的 Docker Compose v2 插件。
服务器上保留仓库文件结构，至少保留 `deploy/docker-compose.yml` 和整个
`deploy/custom/`；自有 Compose 依赖父目录的基础文件。

```sh
git clone git@github.com:wklong/sub2api.git
cd sub2api
# 首次部署建议检出已发布版本，使部署配置与应用版本一致。
git checkout v0.2.23-custom.1
cp deploy/custom/.env.example deploy/custom/.env
chmod 600 deploy/custom/.env
```

编辑 `.env`，填写 `SUB2API_VERSION` 为刚发布的完整版本标签，例如
`v0.2.23-custom.1`。分别执行 `openssl rand -hex 32` 生成 PostgreSQL 密码、
Redis 密码、JWT 密钥和 TOTP 密钥，填写四个不同值。建议填写初始管理员邮箱和密码，
管理员配置仅首次初始化生效。脚本不会生成、显示或覆盖这些凭据。

`.env` 使用 Compose 格式，包含 `$` 或 `#` 等特殊字符的值应使用单引号包裹；
优先使用十六进制随机密码。避免在命令行传入真实密钥。

如果包未公开，先交互执行 `docker login ghcr.io -u wklong`，
按 GitHub 的要求使用有权读取该包的令牌作为密码。公开镜像一般可匿名拉取。

```sh
sh deploy/custom/deploy.sh check
sh deploy/custom/deploy.sh up
sh deploy/custom/deploy.sh status
sh deploy/custom/deploy.sh logs
```

本地刚用 Docker 构建的镜像不需要从 GHCR 拉取，可执行：

```sh
PULL_IMAGES=false sh deploy/custom/deploy.sh up
```

默认 `PULL_IMAGES=true`，适用于服务器按版本从 GHCR 更新镜像。

脚本可以从任意工作目录调用，始终读取脚本所在目录的 `.env`；
Shell 中已导出的同名环境变量会按 Compose 规则覆盖 `.env`。
默认只监听 `127.0.0.1:8080`，建议通过 HTTPS 反向代理访问。
需要外部直连时，在 `.env` 中明确设置 `BIND_HOST`，并配置服务器入站规则。
PostgreSQL 和 Redis 不向宿主机暴露端口。

## 三、持久化、更新和恢复

应用、PostgreSQL、Redis 分别使用命名卷。默认项目名称为 `sub2api-custom`，
例如应用卷为 `sub2api-custom_sub2api_data`；项目名和数据库凭据确定后应保持稳定。
旧部署的数据不会自动迁入这套新卷；迁移现有实例需先确认原卷名称、数据库版本及备份恢复方案。
不要执行 `docker compose down -v`，该参数会删除持久化卷。

更新流程：

1. 记录当前镜像版本、提交和配置；备份 PostgreSQL、应用数据卷及 `.env`。
   PostgreSQL 应使用经过验证的逻辑备份或一致性备份，不要直接拷贝运行中的数据库目录。
2. 发布新的自有镜像，检查更新说明及数据库迁移。
3. 检出新发布标签，使基础 Compose 和自有部署文件与新版源码一致。
   `.env` 被 Git 忽略，仍需检查是否有新增必填配置。
4. 修改 `.env` 中的 `SUB2API_VERSION`，重新执行 `check` 和 `up`。
5. 检查健康状态、登录、充值、支付回调、余额和关键 API。

更新前后都不要重新生成 JWT/TOTP 密钥。镜像构建、数据库迁移或健康检查失败时停止发布，
保留错误日志供排查；脚本失败不会自动回滚。已有数据库迁移时，改回旧镜像不一定兼容，
需要结合旧部署配置和已验证的数据库备份恢复。

旧版后台在线更新会下载上游二进制，不适用于自有部署。新版镜像使用容器升级模式，
只通过下述宿主机升级服务安装自有镜像；升级器未安装时显示提示，禁止回退到上游二进制。

可选配置名称参考 `../.env.example`，实际可传递项以基础 Compose 的
`environment` 为准；仅在 `.env` 添加任意变量不代表它会自动进入应用容器。


## 四、后台按钮升级自己的最新版本

适用于 Linux amd64、Docker Compose v2、systemd、Python 3.9 及更新版本。
目标为本仓库 main **已成功构建且 GHCR 清单实际存在**的最新版本；不读取 Git 标签列表推测镜像。
首次上线需手动部署包含该功能的镜像，并安装一次升级服务。旧实例需先按第三节确认原数据卷和备份。

在仓库根目录执行（先完成数据库备份，并确认 deploy/custom/.env 指向正确项目）：

~~~sh
sudo sh deploy/custom/updater/install.sh
sh deploy/custom/deploy.sh check
sh deploy/custom/deploy.sh up
~~~

在管理员后台点击版本号，刷新版本，核对当前提交、目标提交、构建时间，点击升级并确认。
页面依次显示排队、拉取、重建和健康检查；短暂断线后自动重连。
普通用户无法调用管理员升级和状态接口。应用仅挂载专用 Unix socket，不挂载 Docker socket。
socket 仅允许 root 和 GID 1000 使用，应用镜像使用 GID 1000；不要将升级器端口暴露到公网。

升级器只接受固定仓库、已验证的提交及摘要，不接受任意镜像、命令、路径或 Compose 服务。
它固定镜像摘要，只重建 sub2api 服务，保留 PostgreSQL、Redis 和命名卷。
在线升级会生成本地 online-image.json，deploy.sh 自动加载它，后续重启保持已安装的摘要。
因此在线升级后 .env 的 SUB2API_VERSION 不代表最终实际镜像；以覆盖文件和运行容器为准。
手动切换版本时先停止升级服务，确认无任务执行，备份并移走 online-image.json，
再修改 .env 并执行 check/up，完成后重新启动升级服务。

升级前镜像引用、固定本地镜像 ID 及任务状态保存在 /var/lib/sub2api-updater/status.json；旧覆盖配置保存在
/var/lib/sub2api-updater/previous-image.json（如升级前存在）。拉取失败不会重建应用。
启动或健康检查失败时不自动回滚，因为数据库迁移可能已经执行。按第三节评估数据库兼容性后恢复。
如果新版应用无法启动，后台接口也无法返回任务状态，页面会提示状态未知，请在宿主机检查：

~~~sh
sudo systemctl status sub2api-updater
sudo cat /var/lib/sub2api-updater/status.json
sh deploy/custom/deploy.sh logs
~~~

升级器代码安装在 /opt/sub2api-updater/updater.py。将来升级器自身需要更新时，
在没有活动升级任务时重新执行 install.sh；应用按钮只更新应用镜像。
宿主机升级器配置在 /etc/sub2api-updater.env（root 600），包括 SUB2API_DEPLOY_DIR。
公开仓库/镜像可匿名查询。私有仓库可在该文件加入 UPDATE_GITHUB_TOKEN；
私有 GHCR 可加入 GHCR_USERNAME 和 GHCR_TOKEN，并通过 sudo docker login ghcr.io 配置拉取权限，
然后 sudo systemctl restart sub2api-updater。不要把这些凭据放入前端或提交到仓库。

## 五、manifest unknown 排查

git ls-remote --sort=-version:refname --tags https://github.com/wklong/sub2api.git
仅列出 Git 标签；main 是分支，不出现在该列表里。Git 标签并不保证有同名镜像，
上游遗留标签、未构建提交和缩短的提交哈希尤其不能直接当作 GHCR 标签。
当前发布规则为 main、sha-<40 位完整提交哈希>，或工作流成功发布的 vX.Y.Z-custom.N。
版本标签保留开头的 v；提交标签保留 sha- 前缀且使用完整哈希。

先在 Actions 确认 Custom Image Release 成功，再检查：

~~~sh
docker buildx imagetools inspect ghcr.io/wklong/sub2api:main
# 将下面占位符替换为 Actions 对应的 40 位完整提交哈希：
docker buildx imagetools inspect ghcr.io/wklong/sub2api:sha-<完整提交哈希>
~~~

确认构建确实未发布或清单缺失时，在 Actions 选择 main，勾选 publish_main 后手动运行，
可补发 main 和对应完整提交镜像。默认手动构建不会上传。
发布工作流会检查两个镜像标签的摘要和 linux/amd64 平台，清单验证失败则整个发布失败。
后台升级器也会再次验证 GHCR 镜像；缺失的镜像不会出现在可升级目标中。
