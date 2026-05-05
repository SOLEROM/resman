# resman — Mermaid Architecture Diagrams

---

## 1. System Layers Overview

```mermaid
graph TD
    subgraph BROWSER["🌐 Browser  localhost:5090"]
        SPA["Single Page App\nvanilla JS + xterm.js + Socket.IO"]
    end

    subgraph SERVER["⚙️ Flask Server  Python + eventlet"]
        VR["VaultRegistry\nreads system.yaml"]
        VRT["VaultRuntime\nlaunches Claude / bash sessions"]
        TM["TaskManager\npriority queue · JSONL event log"]
        WS["WindowState\nmanual time-sync · gates tasks"]
        SCH["Scheduler\nAPScheduler cron"]
        TMUX["TmuxManager\nisolated tmux socket"]
        PTY["PtyBridge\nPTY fork → WebSocket stream"]
    end

    subgraph FS["💾 Filesystem"]
        CFG["config/\nsystem.yaml · schedule.yaml\nbudget.json · tasks.jsonl · task-logs/"]
        VAULTS["vault-A/  vault-B/  vault-C/ ...\n(any path on filesystem)"]
        TOOLS["tools/\ningest.sh · new-vault.sh"]
        TPL["wikValTemplate/\nvault scaffold"]
    end

    SPA -- "REST + WebSocket" --> SERVER
    PTY -- "terminal stream" --> SPA
    VR -- "reads / writes" --> CFG
    TM -- "appends events" --> CFG
    TM -- "writes logs" --> CFG
    WS -- "reads / writes" --> CFG
    TMUX -- "tmux sessions" --> VAULTS
    VRT -- "runs tools" --> TOOLS
    TOOLS -- "clones" --> TPL

    WS -- "gates dispatch" --> TM
    TM -- "dispatches via" --> VRT
    VRT -- "creates session" --> TMUX
    TMUX -- "PTY attach" --> PTY
    SCH -- "fires tasks" --> TM
```

---

## 2. Browser UI Layout

```mermaid
graph LR
    subgraph UI["Browser Window"]
        subgraph SIDE["Left Sidebar  (fixed width)"]
            V1["● ai-agents-research  🟢"]
            V2["○ llm-benchmarks      ⚫"]
            V3["○ ml-papers           ⚫"]
            DIV["── unregistered ──"]
            U1["  found-vault    [+ Register]"]
            BTNS["[+ New Vault]   [⚙ Config]"]
        end

        subgraph MAIN["Main Panel  (tabbed)"]
            TABS["[ Terminal ]  [ Docs ]  [ Tasks ]  [ Config ]"]
            subgraph T1["Terminal Tab"]
                XTERM["xterm.js  (live tmux session)"]
                TBAR["[+ Bash]  [+ Claude]  [Open Obsidian]"]
            end
            subgraph T2["Docs Tab"]
                MD["Markdown viewer / editor\nvault README  or  docs/"]
            end
            subgraph T3["Tasks Tab"]
                TQ["Task queue list\npending · running · deferred · done"]
                TQSUB["▶ expand parent → child tasks per vault"]
            end
            subgraph T4["Config Tab"]
                YED["YAML editor\nsystem.yaml · schedule.yaml"]
            end
        end

        subgraph BAR["Bottom Status Bar  (always visible)"]
            WBAR["● ACTIVE  ends in 3h 12m     [ sync ▼ ]"]
        end
    end

    SIDE --> MAIN
    MAIN --> BAR
```

---

## 3. Server Module Dependencies

```mermaid
graph LR
    CFG["ConfigManager\nloads system.yaml"]
    VR["VaultRegistry\nvault list + discovery"]
    VRT["VaultRuntime\nstart/stop sessions"]
    TM["TaskManager\nqueue + state + JSONL"]
    WS["WindowState\nactive/between/ended"]
    SCH["Scheduler\nAPScheduler"]
    TMUX["TmuxManager\ntmux socket"]
    PTY["PtyBridge\nPTY + WebSocket"]
    RT["routes.py\nREST API"]
    WSH["websocket_handlers.py\nSocket.IO events"]

    CFG --> VR
    CFG --> TMUX
    CFG --> WS
    VR --> VRT
    VR --> RT
    WS --> TM
    SCH --> TM
    TM --> VRT
    VRT --> TMUX
    TMUX --> PTY
    RT --> TM
    RT --> VR
    RT --> WS
    WSH --> PTY
```

---

## 4. Task State Machine

```mermaid
stateDiagram-v2
    [*] --> pending : task created\n(window active)
    [*] --> deferred : task created\n(window not active)

    pending --> running : dispatcher picks up task
    running --> completed : exit code 0
    running --> failed : exit code != 0

    pending --> deferred : window ends mid-queue
    deferred --> pending : window activates\n(high / medium priority)
    deferred --> deferred : low priority —\nmanual promote required

    completed --> pending : re-run\n(pre-filled form)
    failed --> pending : re-run\n(pre-filled form)

    completed --> [*]
    failed --> [*]
```

---

## 5. Window State Machine

```mermaid
stateDiagram-v2
    [*] --> between : server starts

    between --> active : user clicks\n"Start window now"
    active --> between : user clicks "End window now"\nor window_ends_at reached

    active --> ended : user clicks\n"End weekly period"
    ended --> active : user clicks\n"Start weekly period"\nthen "Start window now"

    between --> ended : user clicks\n"End weekly period"

    note right of active
        Tasks run normally.
        Cron tasks fire.
        Timer counts down.
    end note

    note right of between
        Tasks queue as deferred.
        Cron tasks are skipped.
        Next window starts on\nfirst user command.
    end note

    note right of ended
        Weekly period closed.
        All tasks deferred.
        Resumes on manual sync.
    end note
```

---

## 6. ALL-Vaults Task Fan-Out

```mermaid
graph TD
    USER["User creates task\nvault: ALL  op: wiki-lint"]
    PARENT["Parent Task\nid: t-001  vault: ALL\nstate: running"]
    C1["Child Task\nid: t-001-a\nvault: ai-agents\nstate: completed ✓"]
    C2["Child Task\nid: t-001-b\nvault: llm-bench\nstate: running ⟳"]
    C3["Child Task\nid: t-001-c\nvault: ml-papers\nstate: failed ✗"]

    LOG1["task-logs/t-001-a.log"]
    LOG2["task-logs/t-001-b.log"]
    LOG3["task-logs/t-001-c.log"]

    RESULT["Parent rolls up:\nstate = failed\n(any child failed)"]

    USER --> PARENT
    PARENT --> C1
    PARENT --> C2
    PARENT --> C3
    C1 --> LOG1
    C2 --> LOG2
    C3 --> LOG3
    C1 --> RESULT
    C2 --> RESULT
    C3 --> RESULT
```

---

## 7. New Vault Creation Flow

```mermaid
sequenceDiagram
    actor User
    participant UI as Browser UI
    participant SRV as Flask Server
    participant FS as Filesystem
    participant OBS as Obsidian

    User->>UI: click [+ New Vault]
    UI->>User: prompt: name + target_path + purpose
    User->>UI: submit
    UI->>SRV: POST /api/vaults/create
    SRV->>FS: git clone wikValTemplate → target_path/
    SRV->>FS: run bin/setup-vault.sh
    SRV->>FS: append vault entry to system.yaml (path: target_path)
    SRV-->>UI: vault registered
    UI->>UI: open Terminal tab for new vault
    UI->>User: "Run /wiki in this Claude session\nto scaffold the wiki structure"
    User->>OBS: open vault in Obsidian (optional)
```

---

## 8. Vault Operation Execution Flow

```mermaid
sequenceDiagram
    actor User
    participant UI as Browser UI
    participant TM as TaskManager
    participant WS as WindowState
    participant VRT as VaultRuntime
    participant TMUX as TmuxManager
    participant PTY as PtyBridge

    User->>UI: click "Ingest URL" → fill form → submit
    UI->>TM: POST /api/tasks  {op: wiki-ingest, url: ..., vault: ai-agents}
    TM->>WS: is window active?
    alt window active
        WS-->>TM: yes
        TM->>TM: append created + started events to tasks.jsonl
        TM->>VRT: dispatch task
        VRT->>TMUX: create session rsm-ai-agents-task-t001
        TMUX->>PTY: attach PTY to session
        PTY-->>UI: stream terminal output via WebSocket
        VRT->>TMUX: send command: ingest.sh vault url
        TMUX-->>VRT: exit code
        VRT->>TM: report completed / failed
        TM->>TM: append completed event to tasks.jsonl
    else window not active
        WS-->>TM: no
        TM->>TM: append deferred event to tasks.jsonl
        TM-->>UI: task queued as deferred
    end
```
