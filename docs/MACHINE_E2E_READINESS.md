# NoticeBot 真机 E2E Readiness 与到货测试清单

> 更新：2026-08-03  
> 分支：`sonen/modification`  
> 目标机器：3× SCS0009、FE-URT2、OV4688 head camera、M5Stack CoreS3、Mac laptop  
> 本文只讨论真机路径；Mac 内置 webcam 不是 blocker。

## 结论

软件路径已经具备一条命令完成目标 E2E 的接线；是否 GO 只剩真机到货后必须实际确认的电气、校准、串口、相机和机械安全：

```text
用户请求 → Gemini 五视角 Plan → 真机 CV watch → 五时间帧 Confirm
→ 只输出一行 feedback
```

当前大致状态：

| 层 | 状态 | 证据 / 缺口 |
|---|---|---|
| CV 模型本机加载 | 🟢 | Grounding DINO + MediaPipe、YOLO-World + MediaPipe 真实图片测试通过 |
| YOLO-World 接口 | 🟢 | 连续推理约 94–132 ms，动态 vocabulary 可用 |
| Gemini API | 🟢 | Flash-Lite、minimal thinking、low resolution、priority tier 真实调用通过 |
| SessionFlow | 🟢 | 逻辑测试全部通过 |
| 真机舵机/CoreS3/camera | ⚪ | 等机器到货才能验证 |
| 当前 `.venv` 硬件依赖 | 🟢 | full `requirements.txt` 已安装，`pip check` 通过 |
| 当前 production Planner | 🟢 | 主循环已调用 Gemini structured output |
| 五张独立 Planning 图片 | 🟢 | sweep 独立图片送 Gemini；grid 只作本地 debug |
| Candidate 五帧 Confirm | 🟢 | `t-1,-.5,0,+.5,+1` 已接在 finding 之前 |
| Plain-line feedback | 🟢 | 默认 `--feedback console`；不会进入 S7/CoreS3 feedback |

因此，明天正确目标是：**先得到每一层的 GO，再进行受控组合**。不要把所有未知量一次接上。

---

## P0：机器到之前的软件准备（已完成）

### P0.1 安装完整硬件运行依赖

当前 `.venv` 已补齐：

```text
scservo_sdk: OK
serial: OK
faster_whisper: OK
google.genai: OK
ultralytics: OK
mediapipe: OK
cv2: OK
```

其中：

- `feetech-servo-sdk`：舵机总线必须。
- `pyserial`：CoreS3 必须。
- `faster-whisper`：如果第一轮使用 Web UI 文字输入，可以暂缓。

安装命令：

```bash
cd /Users/zhangjianan/Desktop/HRI/potato-sidekick
source .venv/bin/activate
pip install -r requirements.txt
pip check
```

完成后至少确认：

```bash
python -c "import scservo_sdk, serial, google.genai, cv2, mediapipe"
```

### P0.2 明确明天测试的是哪条 VLM 路径

现在 production 统一为 Gemini；whole-video 脚本只作独立 smoke：

| 路径 | 当前状态 | 用途 |
|---|---|---|
| Gemini video trigger | 独立脚本、真实 API 已通过 | whole-video API smoke |
| Gemini 五图 Planner + 五图 Confirm | 已接 production 主循环 | 明天真机目标路径 |

如果明天主要测试真机动作和传感链路，建议第一轮设置：

```bash
python noticebot_loop.py ... --offline
```

这样先排除云端模型不确定性；`--offline` 现在同时覆盖 Planner 和 Judge。

真实语义 Planning 只需要 `.env` 中有效的 `GEMINI_API_KEY`。

### P0.3 增加安全的 feedback 模式

之前已经决定此阶段反馈只输出：

```text
[FEEDBACK] 我看到有人开始使用你的电脑了。
```

但当前主循环中，CV finding 会发出 `finding` event，并进入：

```text
S7a found motion → S7b beckon loop
```

这与当前目标不一致，也是首次上电的机械风险。真机自动 E2E 前需要一个明确开关：

```text
--feedback console
```

该模式现已实现，并且是默认值：

- 可以记录 event/feed。
- 可以在终端打印一行反馈。
- 不请求 S7a/S7b。
- 不发 CoreS3 sound/LED feedback。
- 人工按 `7` 测动作仍然保留，便于单独验收机械动作。

### P0.4 准备物理急停

软件 STOP 会回到 S1，但不是电气急停，也不一定立即解除 torque。

到货测试桌上必须有：

- 可以立即断开的独立 5–6 V servo power。
- 一个人盯着 cable loom 和机械干涉。
- 终端保持可输入 `r`/退出。
- 第一次动作从 center、小幅 jog 开始，不能直接跑完整 sweep。

---

## P1：到货后先通物理与电气

### 1. 不上电检查

- 确认 servo IDs 预期为 pan=1、tilt=2、nod=3。
- 确认 FE-URT2 signal slider 为 5 V，不是 3.3 V。
- 确认 servo rail 使用独立 5–6 V，绝不能接 9 V/12 V。
- 确认 USB 和外部 servo power 同时连接的走线正确。
- 手动轻微活动机构，确认没有包装、螺丝、线束卡住。
- 特别检查 pan 轴 camera cable loom；这是现有设计的机械限位来源。
- 确认 horn 没有跨 0/1023 seam。

### 2. 只开逻辑/总线，不做大动作

预期系统可能看到两个 `/dev/cu.usbmodem*`：servo adapter 和 CoreS3。不能靠端口名字猜。

```bash
python robot/tools/check_bus.py
```

Pass：

- ID 1/2/3 都能 ping。
- model id 合理。
- voltage ≥5.0 V；最好 ≥5.5 V。
- temperature 合理。
- 没有位置读数跳过 1023→0。

如果只读到约 4.3 V，说明可能在吃 USB back-feed：能 ping 不代表能安全运动。

### 3. 校准不能直接相信旧数值

当前 `robot/calibration.py` 的 `UNCALIBRATED` 是空集，因此 preflight 会显示 calibrated；但 nod 的注释仍明确写着首次真机需要确认方向。

必须逐轴验证：

1. center 是否真的是机械中位。
2. 正方向是否和 Blender/render 一致。
3. limit 是否在碰撞前留出余量。
4. pan cable 在 ±60° sweep 时是否被拉紧。
5. nod clip 是抬头还是低头，确认 `INVERT["nod"]`。

不要因为 preflight 显示 GO 就跳过这一步。

---

## P2：逐个通执行器和 CoreS3

### 1. 舵机逐轴

顺序：

```text
pan 小幅 jog → tilt 小幅 jog → nod 小幅 jog → center → 单个 clip → 全状态 clips
```

每一步观察：

- 实际方向。
- 是否撞限位。
- 电压是否掉落。
- servo 温度和异常噪声。
- pan camera cable 是否扭转。

单 clip 从静态/小动作开始，S4 sweep 和 S7 beckon 最后测。

### 2. CoreS3

必须验证协议握手：

```text
laptop → EVT PING
CoreS3 → IN PONG cores3_sidekick v2
```

然后逐项测：

- PTT_DOWN / PTT_UP。
- OK。
- STOP。
- BODYTAP。
- 9 个 UI screens。
- LED hue / level。
- speaker volume / sfx。

注意：`session/cores3_link.py` 底部的 standalone demo 仍发送 `ARMED/WATCH/FOUND/KEEP/STEP` 等已删除旧命令，因此它现在不能作为可信验收脚本。应先更新为 v2 命令或直接通过主循环/一个新的协议 smoke test 验证。

### 3. 两个串口同时存在

必须验证：

- `open_bus()` 找到 servo adapter。
- `find_cores3(exclude=(servo_port,))` 找到 CoreS3。
- 重新插拔或换 USB 口后仍能重新发现。
- CoreS3 PING 不会误发给 servo adapter。

---

## P3：通头部相机与 sweep

Mac 内置 webcam 不重要，但 head camera 必须验证。

```bash
python noticebot_loop.py --list-cams
```

Pass：

- 找到 OV4688 对应 index。
- 能取得非 square、预期分辨率的稳定画面。
- 机械运动时 USB cable 不掉线。
- S4 的 5 个 settled station 都捕获到 raw frame。
- station 间距约 30°，覆盖 −60°…+60°。
- frame 只在 `settled_ms > SETTLE_MS` 时进入 Planning。
- 每张图对应的 pan angle 正确，没有“已经到了下一角度却标成上一角度”。

建议同时保存并人工检查：

```text
session_feed/sweeps/<timestamp>/panorama.jpg
session_feed/sweeps/<timestamp>/pan_*.jpg
session_feed/sweeps/<timestamp>/plan.json
```

必须确认 overlay 只用于 debug；送给 VLM 的 planning 图必须是 raw frame。

---

## P4：通真机 CV

### 默认路径

```text
Grounding DINO + MediaPipe，约 1 Hz
```

运行时重点不是追求高 FPS，而是检查：

- detector vocabulary 能被 Plan 更新。
- person 和目标物在真实相机距离下可见。
- face/pose 在机器人实际 FOV 和高度下稳定。
- 运动停止后 CV 才处理图像。
- CV 推理不会让 servo/CoreS3 heartbeat 饿死。

### 低延迟备选

```bash
--detector yoloworld --cv-hz 4
```

YOLO-World 本机静态图已经通过，但真机还要验证：

- 目标 vocabulary 在实验环境中的召回率。
- 多词标签，例如 `cell phone`、`robot arm`。
- `set_vocab()` 在重新 Planning 后真的更新。
- 模型切换/首次加载不会发生在参与者等待期间。

### 关系触发必须逐类测

不要一开始测 11 种。明天先选三个最容易观察的：

1. ID 3 eye-contact：只需要 face。
2. ID 9 hands-on：person pose + 明确物体。
3. ID 10 gathering：person count change。

然后再测：

- ID 1 gaze-at。
- ID 4 pointing。
- ID 7 approach/depart。
- ID 11 handoff。

已知语义风险仍存在：当前 truth vector 是全局 bool，object binding 对 ID 8/9/11 等还不严格；`then` 可能退化成 AND。它们在修复前不能作为最终精度验收。

---

## P5：通 Planning

### 当前 production 路径

```text
5 个独立 sweep frames → Gemini structured output → validated watch spec
```

要检查：

- `seen/detect/focus/boxes/watch` 都存在。
- richest pan 与人工判断大致一致。
- Planner 返回 validation violations 时不能静默安装坏 spec。
- STOP 或新的 request 出现后，旧 planner thread 的结果不能覆盖新状态。

后两项目前是已知代码风险，不应跳过。

Planner 结果带 request generation；STOP 或新请求后，迟到结果会被丢弃。第一次 invalid 会重试一次，仍 invalid 则拒绝安装。

---

## P6：通 Candidate → Confirm → Feedback

目标链路：

```text
CV RelationFacts
→ entity/target match
→ persist
→ false→true onset
→ strict sequence
→ cooldown
→ 收集 onset 前后 5 张时间帧
→ Gemini Confirm
→ confirmed=true
→ console feedback
```

当前已经通：

- strict `then`，不会退化成 AND。
- raw-frame JPEG ring buffer。
- 5 张时间图片的 Gemini Confirm。
- Confirm 位于 finding/S7 之前。
- `--feedback console` adapter。
- gaze/point/lean/hands-on/approach/handoff 的 target-label gate。

仍需后续升级的是跨多人/同类多物体的稳定 instance ID；明天单人、单目标 E2E 不受此限制。

因此明天可以手动用 `f` 验证 finding/story plumbing，但不能把它当成目标自动 E2E 已通过。

---

## P7：并发、恢复与长时间稳定性

各组件单独通过后还要通这些边界：

| 场景 | 预期行为 |
|---|---|
| Gemini 请求进行中按 STOP | 立即取消当前 task；迟到结果不能重新安装 |
| Planning 中 CoreS3 STOP | 回 S1，清空 sweep/spec |
| Camera 短暂掉线 | 不运动到错误角度，不生成 candidate，清晰报错 |
| CV 推理异常 | motion/UI 继续，研究者仍可人工触发 |
| Gemini timeout/invalid JSON | 不安装坏 Plan，不进入 S7 |
| Servo 电压过低 | preflight NO-GO，不开始 session |
| CoreS3 断开 | 主程序不中断 servo safety；日志明确 |
| 同一事件持续很久 | 只触发一次，必须 break/re-form 且 cooldown 结束 |
| 多人/多物体 | subject/target 不串线 |
| 程序退出时 story 尚在 finalize | flush/join，最后事件不丢失 |

最后做至少 20–30 分钟 soak test，观察：

- camera 是否持续有帧。
- 串口是否断连。
- servo 温度。
- 内存是否持续增长。
- CV/Gemini thread 是否堆积。
- feed/sweep 文件是否落盘完整。

---

## 明天推荐执行顺序

| 顺序 | 测试 | 允许动作 | GO 条件 |
|---:|---|---|---|
| 0 | 视觉/接线检查 | 不上电 | 无干涉、供电明确 |
| 1 | Servo bus | 只读/ping | 3 IDs、正确电压温度 |
| 2 | Calibration | 小幅单轴 | center、方向、limits 正确 |
| 3 | Motion clips | 单 clip | 无碰撞、无掉压 |
| 4 | CoreS3 | UI/input only | v2 PING 和全部输入通过 |
| 5 | Head camera | camera only | 正确 index、稳定画面 |
| 6 | S4 sweep | 慢速受控 | 5 station raw frames 正确 |
| 7 | CV | 不自动触发动作 | person/target/pose 可见 |
| 8 | Offline state E2E | console/manual finding | S1→S7→S5 可控 |
| 9 | Cloud Planning | 先旧路径或 Gemini 单测 | valid Plan、正确 richest view |
| 10 | 自动 Trigger | console feedback only | 一次正确 trigger，无误动作 |
| 11 | Soak | 安全模式 | 20–30 分钟稳定 |

---

## 最小真机命令路线

依赖完成后：

```bash
cd /Users/zhangjianan/Desktop/HRI/potato-sidekick
source .venv/bin/activate

# 1. 找 head camera
python noticebot_loop.py --list-cams

# 2. 总线和 calibration/clips/camera 总检查
python robot/tools/preflight.py --cam <HEAD_CAM_INDEX>

# 3. 第一轮排除 STT 和云端 Planner，使用 Web UI 文字输入
python noticebot_loop.py \
  --cam <HEAD_CAM_INDEX> \
  --cores3 \
  --no-stt \
  --detector gdino \
  --cv-hz 1 \
  --offline \
  --serve

# 4. Offline GO 后，运行真实 Gemini E2E
python noticebot_loop.py \
  --cam <HEAD_CAM_INDEX> \
  --cores3 \
  --no-stt \
  --detector gdino \
  --cv-hz 1 \
  --feedback console \
  --serve
```

默认 console feedback 不会自动请求 S7；只有显式传 `--feedback robot` 才允许确认事件进入机器人反馈动作。

---

## 明天的最终 GO / NO-GO

### Hardware bring-up GO

- 三个 servo ping、voltage、temperature 正常。
- pan/tilt/nod center、方向、limits 人工确认。
- CoreS3 v2 PING、PTT、OK、STOP、BODYTAP 正常。
- OV4688 在完整 sweep 中不掉线。
- preflight 显示 GO。

### Software-on-machine GO

- S1–S8 clips 单独运行安全。
- S4 得到 5 个正确角度的 raw frame。
- Grounding DINO 或 YOLO-World 能在真机视角检测 person 和目标物。
- MediaPipe 至少能稳定输出 face/pose。
- offline Plan 能进入 S5，STOP 能可靠清空任务。
- 运行 20–30 分钟无串口/camera/thread 堆积问题。

### Target architecture E2E GO

只有以下全部完成才可以宣布：

- Gemini 五张独立空间图片 Planner 已进入主循环。
- PlanV2 validation 失败时不会 install。
- CV 输出 bound RelationFacts。
- strict sequence、onset、persist、cooldown 通过测试。
- 五张时间图片 Gemini Confirm 位于 finding 前。
- `confirmed=false` 不反馈。
- `confirmed=true` 只输出一行 feedback。
- 默认不会触发 S7/LED/sound 等未授权动作。

## 当前判断

软件已经整理到**接上真机即可分层 E2E**：Gemini Planner、独立空间图片、CV candidate、五时间帧 Confirm 和 console feedback 已贯通并分别测试。剩余未知量是必须依赖实物确认的供电、calibration、双串口、head-camera index 和线束机械安全。
