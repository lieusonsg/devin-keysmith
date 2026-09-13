# devin-keysmith

向 **Devin CLI** 安装一条可撤销的指令。先预览,确认后才写入。

Keysmith 将指令安装到本地 AI 编码工具上:预览、写入、验证、移除。

`devin-keysmith` 面向 **Devin CLI**(`devin`,已针对 3000.10.21 版本验证)。
安装后,新会话将遵循该指令。本工具不会修改 Devin 二进制、插件注册表或缓存、
`credentials.toml`、会话数据库、`state.vscdb`、`argv.json`,也不读取任何账号
或凭据。

> [!IMPORTANT]
> 该变更只影响安装后启动的**新会话**。默认仅展示计划;`--yes` 才写入。
> 安装后请新开一个 Devin 会话。

## 用法

1. **预览。** 确认前不写入任何内容。
2. **安装。** `devin-keysmith.py install --yes`
3. **新开 Devin 会话。** 只对新会话生效。
4. **随时移除。** `devin-keysmith.py uninstall --yes`

```bash
python devin-keysmith.py install                 # 预览,不写入
python devin-keysmith.py install --yes           # 应用(global 模式)
python devin-keysmith.py status                  # 查看状态
python devin-keysmith.py doctor                  # 校验宿主与清单
python devin-keysmith.py uninstall --yes         # 撤销
```

## 四种部署目标

Devin 暴露多个指令面;`devin-keysmith` 每次部署只写入其中一个,用 `--mode`
选择:

| 模式 | 目标 | 语义 |
|---|---|---|
| `global`(默认) | `~/.codeium/windsurf/memories/global_rules.md` | 用户级常驻规则(`[Windsurf]` 提供方,规则名 `global_rules`),注入每个会话。**为何默认**:插件自带的 `AGENTS.md` 规则(如 superpowers)在真实会话中会按名字遮蔽所有 AGENTS 同名规则——已实测验证;而此规则名永不冲突。 |
| `append` | `~/AGENTS.md` | 用户级常驻规则(`[Standard]` 提供方)。追加带标记的块;原有内容保持不变,重装时原位替换该块。仅当没有已安装插件携带根 `AGENTS.md` 时可靠(`doctor` 会报告)。 |
| `plugin` | `~/.devin-keysmith/plugins/devin-keysmith-instruction/`,经 `devin plugins install <path> --local -y` 注册 | Devin 原生插件,携带常驻 `AGENTS.md` 规则(`--include-skill` 可附带 `skills/`)。卸载时经 CLI 反注册并删除脚手架。**Windows 注意**:devin CLI 会将本地插件源以符号链接接入缓存,需要符号链接权限——请开启 Windows 开发者模式,或从提权终端运行(已验证的限制,3000.10.21 版本;`doctor` 会报告此状态)。 |
| `project` | `<git-root>/AGENTS.md` | 项目层——Devin 从当前目录向上查找至 `.git` 边界(已验证向上查找;非 git 目录不生效)。 |
| `skills` | `%APPDATA%\devin\skills\<name>\SKILL.md`(Windows)/ `~/.config/devin/skills/…` | 用户级技能,含 `name`/`description` frontmatter;按需(`[user, model]`)触发。 |

```bash
python devin-keysmith.py install --mode plugin --yes
python devin-keysmith.py install --mode project --cwd /path/inside/repo --yes
python devin-keysmith.py install --mode skills --name my-instruction --file ./my.md --yes
```

## 已验证的指令面(Devin CLI 3000.10.21,build 611c1cba)

| 指令面 | 位置 | 激活方式 |
|---|---|---|
| Standard 规则 | `~/AGENTS.md`、`<git-root>/AGENTS.md` | 常驻 |
| Claude 规则 | `~/.claude/CLAUDE.md` | 常驻(对本工具只读) |
| Windsurf 规则 | `.windsurf/rules/*.md`、`~/.codeium/windsurf/memories/global_rules.md` | 常驻(对本工具只读) |
| Cursor 规则 | `.cursor/rules/*.md` | 条件触发 |
| 用户技能 | `%APPDATA%\devin\skills\<n>\SKILL.md`、`%APPDATA%\cognition\skills\`、`~/.agents/skills/` | 按需 |
| 插件 | `.devin-plugin/plugin.json` + 根 `AGENTS.md`;注册表位于 `%APPDATA%\devin\cli\plugins\` | 规则常驻,技能按需 |

`devin rules list`、`devin skills list`、`devin plugins list` 可查看 Devin
当前识别的内容——可用于验证部署。

## 安全保证

- **先预览。** 没有 `--yes` 不写入、不调用 Devin CLI。预览会打印将执行的
  完整 `devin plugins install` 命令。
- **清单归属。** 工具创建或修改的每个文件都记录在
  `~/.devin-keysmith/manifest.json` 中,含前后哈希与带时间戳的备份。未纳管
  的文件绝不触碰。
- **原子写入。** 临时文件 + 原子改名;崩溃不会留下半个文件。
- **可逆。** `uninstall --yes` 恢复变更前字节或删除新建文件;`recover --yes`
  修复被中断的安装;`--force` 在替换前完全回滚先前部署(含插件反注册)。
- **捆绑内容锁定。** 捆绑指令带固定 SHA-256;捆绑被篡改时在任何写入前拒绝。
- **宿主保护。** Devin 可执行文件、`_versions/`、插件注册表/缓存、
  `credentials.toml`、`sessions.db*`、`state.vscdb`、`argv.json`、`.bin`
  缓存均为只读,每次操作强制校验。凭据形态的值在所有输出与清单中被脱敏。

## 环境变量覆盖(测试用)

| 变量 | 作用 |
|---|---|
| `DEVIN_KEYSMITH_HOME` | 覆盖用于解析所有根目录的用户主目录 |
| `DEVIN_KEYSMITH_SKILLS_ROOT` | 覆盖技能部署根目录 |
| `DEVIN_KEYSMITH_DEVIN_BIN` | 覆盖 plugin 模式使用的 Devin CLI 可执行文件 |
| `DEVIN_KEYSMITH_MEMORIES_ROOT` | 覆盖 Windsurf memories 根目录(global 模式) |
| `DEVIN_KEYSMITH_PLUGINS_CACHE` | 覆盖用于遮蔽检测的插件缓存路径 |

plugin 模式的二进制查找顺序:`DEVIN_KEYSMITH_DEVIN_BIN` → `PATH` →
`%LOCALAPPDATA%\devin\cli\bin\devin.exe`。

## 测试

```bash
python -m pytest tests/ -q
```

测试套件完全运行在夹具目录与伪造的 Devin CLI 上,绝不触碰真实 Devin 状态。
Python 3.8+,仅标准库(测试使用 pytest)。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功(包括有意的空操作) |
| 1 | 预期内失败(模式错误、缺少 git 根、CLI 注册失败) |
| 3 | 拒绝:目标路径为宿主管理/只读 |
| 4 | 捆绑指令哈希不匹配 |
| 5 | 已存在受管部署(使用 `--force` 或先卸载) |

## 许可

MIT——见 [LICENSE](LICENSE)。
