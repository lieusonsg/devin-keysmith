# Hướng dẫn cài đặt devin-keysmith v0.1.1

> Tài liệu này khớp với bản build đã verify trên máy (Devin CLI 3000.10.21,
> build 611c1cba, Windows). Mọi chế độ đều **preview trước — ghi sau**,
> manifest-owned, và gỡ được sạch từng byte.

## 1. Yêu cầu

| Thành phần | Ghi chú |
|---|---|
| **Devin CLI** | Đã cài và đã `devin auth` login |
| **Python** | 3.8+, chỉ stdlib — không cần cài thêm package nào |
| **Git** | Nếu clone từ GitHub |

## 2. Lấy tool

```bash
# Từ GitHub (khuyến nghị)
git clone https://github.com/lieusonsg/devin-keysmith.git
cd devin-keysmith

# Hoặc dùng bản local
cd E:\tool\keysmith\devin-keysmith
```

## 3. Cài nhanh (3 bước)

```bash
python devin-keysmith.py install          # BƯỚC 1: xem plan — chưa ghi gì
python devin-keysmith.py install --yes    # BƯỚC 2: ghi thật
# BƯỚC 3: mở session Devin MỚI — instruction chỉ hiệu lực với session mới
```

Mode mặc định là `global`: ghi block có marker vào rule always-on toàn cục
`~/.codeium/windsurf/memories/global_rules.md` — rule `[Windsurf]` mà Devin inject
vào **mọi session**, tên rule không bao giờ bị plugin tranh chỗ.

> Tại sao mặc định là `global`? Vì transcript session thật đã chứng minh: plugin
> mang `AGENTS.md` riêng (ví dụ superpowers) sẽ **che toàn bộ** rule tên `AGENTS`
> của user — file vẫn hiện trong `devin rules list` nhưng không bao giờ tới session.

## 4. Bốn lựa chọn khác (mỗi lần install đúng một mode)

```bash
# append — block vào ~/AGENTS.md (rule [Standard])
# ⚠ CHỈ đáng tin khi KHÔNG có plugin nào mang AGENTS.md (xem doctor)
python devin-keysmith.py install --mode append --yes

# project — block vào AGENTS.md ở root của git repo (chạy từ trong repo)
python devin-keysmith.py install --mode project --yes

# skills — skill on-demand, gọi bằng /<ten-skill>
python devin-keysmith.py install --mode skills --name my-rule --yes

# plugin — scaffold + đăng ký qua chính CLI của Devin (always-on, sạch nhất)
# ⚠ Windows: cần bật Developer Mode hoặc terminal nâng quyền (chi tiết mục 8)
python devin-keysmith.py install --mode plugin --yes
```

**Dùng instruction riêng thay vì bundle mặc định:**

```bash
python devin-keysmith.py install --file .\my-instruction.md --yes
```

## 5. Kiểm tra sau khi cài

```bash
# Trạng thái deployment (mode, target, hash)
python devin-keysmith.py status

# Health check đầy đủ
python devin-keysmith.py doctor

# Xác nhận bằng chính CLI của Devin
devin rules list                      # global_rules [Windsurf] always-on
devin rules show global_rules         # thấy nội dung block
devin skills list                     # nếu dùng mode skills

# Test sống (tốn 1 session)
devin -p "One short line: what is your name and who named you?"
```

`doctor` tự báo: thiếu binary Devin, thiếu symlink privilege (plugin mode),
và **plugin nào đang che AGENTS** — khi đó mode append/project vô hiệu,
dùng `global`.

## 6. Gỡ sạch / thay thế

```bash
python devin-keysmith.py uninstall --yes        # trả file về đúng từng byte cũ
python devin-keysmith.py recover --yes          # sửa install bị gián đoạn
python devin-keysmith.py install --force --yes  # thay deployment mới đè cái cũ
```

Plugin mode khi uninstall sẽ tự `devin plugins remove` rồi xoá scaffold.

## 7. Chạy test (tùy chọn — không đụng Devin thật)

```bash
python -m pytest tests/ -q     # 46 tests xanh, dùng fake devin binary + fixture dirs
```

## 8. Khắc phục sự cố

| Triệu chứng | Nguyên nhân & cách xử lý |
|---|---|
| Plugin mode lỗi `os error 1314` (symlinking) | Devin symlink plugin local vào cache — Windows đòi symlink privilege. Bật **Settings → Privacy & security → For developers → Developer Mode** hoặc chạy terminal nâng quyền. Các mode khác không bị ảnh hưởng. |
| Cài `append`/`project` nhưng Devin không theo instruction | Có plugin mang `AGENTS.md` đang che rule của bạn (`doctor` sẽ nêu tên). Chuyển sang `--mode global`. |
| `exit code 4` khi install | `examples/system-role.md` bị sửa, lệch SHA-256 pin. Khôi phục file hoặc cập nhật `BUNDLED_PROMPT_SHA256` có chủ đích rồi deploy lại. |
| `exit code 5` | Đã có deployment. Dùng `--force` hoặc `uninstall --yes` trước. |
| Install đứt giữa chừng (mất điện, Ctrl-C) | `python devin-keysmith.py recover --yes` — rollback theo transaction đã ghi. |
| Đổi instruction đúng cách | Sửa `examples/system-role.md` → tính SHA-256 mới → cập nhật `BUNDLED_PROMPT_SHA256` trong `devin-keysmith.py` → `install --force --yes`. |

## 9. Mã lỗi

| Code | Ý nghĩa |
|---|---|
| 0 | Thành công (kể cả no-op có chủ đích) |
| 1 | Lỗi thường (mode sai, thiếu git root, CLI register fail) |
| 3 | Từ chối ghi vào path host-managed (credentials, sessions.db, state.vscdb…) |
| 4 | Bundle bị sửa — SHA-256 lệch pin |
| 5 | Đã có deployment — dùng `--force` hoặc uninstall trước |

## 10. Biến môi trường (test/automation)

| Biến | Tác dụng |
|---|---|
| `DEVIN_KEYSMITH_HOME` | Override user home (mọi root chạy theo) |
| `DEVIN_KEYSMITH_SKILLS_ROOT` | Override skills root |
| `DEVIN_KEYSMITH_MEMORIES_ROOT` | Override memories root (global mode) |
| `DEVIN_KEYSMITH_DEVIN_BIN` | Đường dẫn devin.exe cho plugin mode |
| `DEVIN_KEYSMITH_PLUGINS_CACHE` | Override plugin cache dùng cho shadow detection |

---

**An toàn:** tool không bao giờ đụng binary Devin, plugin registry, `credentials.toml`,
session DB, `state.vscdb`, `argv.json`, cache `.bin`; mọi ghi đều atomic + backup +
manifest; giá trị hình-credentials bị redact khỏi mọi output và manifest.
