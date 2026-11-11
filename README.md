# Orbit — Multi-Agent Mission Control

Dashboard cục bộ cho **LangGraph + OpenCode V2**, gồm văn phòng 2D, topology trực tiếp, terminal events, task board, duyệt thao tác và checkpoint SQLite. Runtime không dùng dữ liệu giả; mọi agent suy luận qua model đã cấu hình trong OpenCode.

## Chạy trên Windows

Yêu cầu: Node.js 22+, Python (script dùng `uv` để tạo môi trường Python 3.13), OpenCode **V2** đã kết nối provider/model.

```powershell
.\start.ps1
```

Script tạo `.env` từ `.env.example`, cài dependencies có lock, khởi động OpenCode nếu cổng cấu hình chưa hoạt động, backend `127.0.0.1:8000`, frontend `127.0.0.1:3000` trong các cửa sổ riêng.

Khi tạo engine riêng mà password đang trống, script tạo password ngẫu nhiên trong `.env` và truyền cho cả engine lẫn backend. File này đã được gitignore.

```powershell
# Những lần sau
.\start.ps1 -SkipInstall
```

Mở **http://127.0.0.1:3000**, nhập đường dẫn tuyệt đối tới thư mục dự án, mô tả yêu cầu và chọn **Dispatch mission**. Thư mục phải tồn tại trên máy chạy backend và OpenCode. Chỉ một mission thực thi tại một thời điểm.

### Kết nối engine

Chỉnh `.env` nếu dùng server hiện có:

```dotenv
OPENCODE_SERVER_URL=http://127.0.0.1:4096
OPENCODE_SERVER_PASSWORD=
OPENCODE_SERVER_TOKEN=
OPENCODE_AGENT=build
OPENCODE_MODEL=
```

- Basic Auth sử dụng username `opencode`. Bearer token, nếu có, được ưu tiên.
- Ô password trên Web chỉ cập nhật bộ nhớ backend, không lưu vào trình duyệt hay SQLite.
- Để tìm cổng dịch vụ V2 hiện có: `opencode service status`.
- Không cần LLM API key riêng. `OPENAI_API_KEY` là placeholder dành cho việc bổ sung provider LangChain sau này; hiện không được sử dụng.
- `OPENCODE_MODEL=provider/model` tùy chọn; bỏ trống để dùng model mặc định của OpenCode.

## Kiến trúc

```text
React / Zustand / Framer Motion / React Flow
          │ REST + SSE (event ID, reconnect, replay)
FastAPI ──┼── SQLite mission registry + event journal
          └── LangGraph StateGraph + AsyncSqliteSaver
                Supervisor → Planner / Researcher
                           → Approval → Coder → Supervisor
                           → Reviewer → Supervisor → FINISH
                                 └── QA fail → Coder (giới hạn)
                         │
                    httpx bridge
                         │
              OpenCode V2 HTTP + SSE → model / files / tools
```

Supervisor trả về task decomposition và quyết định routing dạng JSON được Pydantic kiểm tra. Graph đảm bảo planner/researcher chạy trước coder và chỉ hoàn tất khi reviewer báo pass với bằng chứng. Mỗi lần chạy specialist có session riêng; context được truyền trong checkpoint, tránh trộn log/quyền của các vai trò.

### Hợp đồng OpenCode V2

Tích hợp theo [API hiện tại](https://opencode.ai/docs/api):

| Chức năng | Endpoint |
|---|---|
| Health | `GET /api/info` |
| Agent discovery | `GET /api/agent` |
| Session | `POST /api/session`, `GET /api/session/{id}` |
| Prompt | `POST /api/session/{id}/prompt` |
| Chờ idle | `POST /api/experimental/session/{id}/wait` |
| Kết quả | `GET /api/session/{id}/message` |
| Live events | `GET /api/event` |
| Duyệt tool | `GET /api/session/{id}/permission`, `POST /api/session/{id}/permission/{requestID}/reply` |
| Dừng | `POST /api/session/{id}/interrupt` |

Các endpoint `/session`, `/prompt_async` và SDK `@opencode-ai/sdk` trong prompt ban đầu thuộc API khác. Dự án này **yêu cầu V2**, không âm thầm chuyển sang V1.

### Đồng bộ terminal

OpenCode server thực thi tools; cửa sổ `opencode serve` chủ yếu hiển thị log server. Muốn xem giao diện terminal cùng session:

```powershell
opencode --server http://127.0.0.1:4096
```

Mở session có tiêu đề `Mission <id> · <role>` tương ứng. Dashboard cho phép copy session ID ở chân terminal. Web hiển thị public text, tool input/progress/result từ OpenCode, bao gồm output mà tool cung cấp; không phải PTY tương tác hay stdout byte-for-byte. Không hiển thị private reasoning. SSE là transport được sử dụng; không cần WebSocket cho các luồng này.

Nếu engine yêu cầu xác thực, đặt biến `OPENCODE_PASSWORD` của cửa sổ CLI bằng `OPENCODE_SERVER_PASSWORD` trong `.env`. Có thể mở thẳng session bằng `opencode --server http://127.0.0.1:4096 --session <session-id>`.

### Approval và recovery

- **Human approval:** LangGraph `interrupt()` trước mỗi lượt coder; kế hoạch và state được checkpoint. Tool-level requests tiếp tục được duyệt **Allow once / Reject** qua OpenCode.
- **Auto-run:** bỏ bước duyệt kế hoạch; đọc/tìm kiếm và một số lệnh kiểm thử thường dùng được phép. Mutation, shell không có trong allowlist và truy cập ngoài thư mục vẫn hỏi ở tool boundary. Không dựa vào regex để phán đoán lệnh nào “nguy hiểm”.
- Planner/researcher/supervisor có rules read-only. Subagent và tool question bị chặn trong session để quyền duyệt tập trung ở dashboard.
- Session rules vẫn chịu cấu hình/policy của OpenCode. Không phải sandbox OS.
- Reload Web khôi phục mission gần nhất và phát lại event journal; API hỗ trợ `Last-Event-ID`.
- Backend restart đánh dấu mission đang chạy là `interrupted`. **Resume** dùng checkpoint và execution receipts để chờ/lấy kết quả session đã submit, tránh gửi lại các bước đã hoàn tất.
- Nếu backend dừng đúng khoảng giữa gửi prompt và ghi receipt, trạng thái `submitting` là không xác định: hệ thống báo lỗi và không tự gửi lại. Kiểm tra session được chỉ rõ trước khi tạo mission mới.
- **Cancel** ngắt session đang chạy, không rollback file đã sửa. Checkpoint history là lịch sử trạng thái đọc được; không rollback filesystem hay tự chạy lại tool trong quá khứ.
- OpenCode SSE là live-only: sự kiện trong lúc engine mất kết nối có thể bị thiếu. Dashboard vẫn lấy trạng thái/quyền đang chờ qua API và đọc kết quả cuối từ message history; không tự tạo lại các token bị mất.

## Kiểm tra

```powershell
.venv\Scripts\python.exe -m pytest backend/tests -q
.venv\Scripts\python.exe -m compileall -q backend
cd frontend
npm run build
```

Test doubles chỉ nằm trong `backend/tests`, kiểm tra pause/resume qua SQLite reopen, reject, QA loop, giới hạn retry, replay SSE và chống duplicate submission. Chúng không thay thế việc xác thực model/provider thật.

Smoke test opt-in dùng model thật, tạo dự án tạm riêng, duyệt kế hoạch và các thao tác của bài kiểm tra, xác nhận file và QA (có thể phát sinh phí provider):

```powershell
.venv\Scripts\python.exe -m backend.tests.live_smoke
```

Nếu tất cả fallback đều trả `HTTP 402`, cần bổ sung số dư hoặc kết nối provider khả dụng trong OpenCode.

### Tự chuyển model khi hết quota

Mặc định `OPENCODE_AUTO_FALLBACK=true`: khi session thất bại với lỗi quota **402** hoặc rate-limit **429**, backend lấy catalog model thật theo thư mục dự án và chuyển model ngay trong session hiện tại. Lịch sử tool được giữ lại; prompt tiếp tục yêu cầu không lặp lại thao tác đã xong. Đây không phải bảo đảm exactly-once cho những hành động mà model tự quyết định chạy lại.

- Tự tìm các chat model enabled có tool support. Catalog khả dụng không có nghĩa là còn số dư; model dự phòng vẫn có thể báo hết quota.
- Với 402, ưu tiên provider khác; bỏ model trả phí dùng cùng provider/ví đã hết tiền. Model miễn phí cùng provider vẫn có thể được thử.
- Tối đa `OPENCODE_MAX_FALLBACKS=4` lần đổi cho mỗi bước. Lịch sử thử lưu SQLite, Resume không đặt lại giới hạn.
- Model chạy thành công được ưu tiên cho các bước tiếp theo trong cùng mission khi backend còn chạy.
- Mỗi lần chuyển có event `model_fallback` trong terminal của agent.
- Không tự chuyển vì lỗi test/code, lỗi ngữ cảnh dài, lỗi mạng chưa rõ kết quả hoặc lỗi xác thực.

Có thể giới hạn danh sách theo thứ tự (dùng ID thật trong `/models` của OpenCode):

```dotenv
OPENCODE_AUTO_FALLBACK=true
OPENCODE_FALLBACK_MODELS=provider-a/model-id,provider-b/model-id#variant
OPENCODE_MAX_FALLBACKS=4
```

Để trống `OPENCODE_FALLBACK_MODELS` thì tự khám phá. Việc chuyển provider gửi context của session sang provider đó và dùng quota/chi phí của tài khoản tương ứng. Khởi động lại backend sau khi sửa `.env`. Mission cũ bị 402 có thể bấm **Resume** để thử fallback; không cần gửi lại prompt gốc.

## Cấu trúc

```text
backend/app/bridge.py   OpenCode HTTP, SSE, quyền và execution receipts
backend/app/graph.py    StateGraph, supervisor, specialists, interrupt
backend/app/main.py     API, lifecycle, streaming, resume/cancel
backend/app/store.py    SQLite registry và journal
frontend/src/Office.tsx Văn phòng chuyển động và React Flow
frontend/src/App.tsx    Dashboard, terminals, tasks, prompt, approvals
frontend/src/store.ts   Zustand, event reducer
data/                  SQLite runtime (gitignored)
start.ps1              Khởi động Windows
```

Đây là ứng dụng **single-user trên loopback**. Chưa có multi-tenant authentication, queue phân tán, retention journal hay triển khai production public Internet. Không bind backend ra Internet trực tiếp. API docs của backend: http://127.0.0.1:8000/docs.
