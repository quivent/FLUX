// ../../../gemstone/governor-interface/src/governor-ui/components.ts
function governorAnchorButton(id, label, active, project = false) {
  const classes = ["anchor", active ? "active" : "", project ? "project" : ""].filter(Boolean).join(" ");
  return `<button class="${classes}" data-anchor="${id}" type="button">${escapeHtml(label)}</button>`;
}
function governorStatusLines(rows, className = "status-lines") {
  return `
    <div class="${escapeHtml(className)}">
      ${rows.map(([key, value]) => `<div><span>${escapeHtml(key)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}
    </div>
  `;
}
function governorMessageView(message, labels = {
  assistant: "Governor",
  user: "Josh",
  system: "System"
}) {
  const label = labels[message.role] || message.role;
  return `
    <article class="message ${message.role}">
      <div class="message-head">
        <span>${escapeHtml(label)}</span>
        <time>${escapeHtml(message.at)}</time>
      </div>
      ${message.role === "assistant" ? renderMarkdown(message.content) : `<pre>${escapeHtml(message.content)}</pre>`}
      ${message.role === "assistant" && message.tools?.length ? governorToolReceiptsView(message.tools) : ""}
    </article>
  `;
}
function governorToolReceiptsView(receipts) {
  return `
    <div class="tool-receipts" aria-label="Governor tool calls">
      ${receipts.slice(-10).map((receipt) => {
    const state2 = receipt.status || (receipt.ok ? "ok" : "failed");
    const stateLabel = state2 === "running" ? "run" : state2 === "ok" ? "ok" : "fail";
    const detail = receipt.detail || (state2 === "running" ? "waiting for result" : "");
    const body = [
      receipt.input ? `Input:
${receipt.input}` : "",
      receipt.output ? `Output:
${receipt.output}` : ""
    ].filter(Boolean).join("\n\n");
    return `
            <details class="tool-receipt ${state2}" ${state2 === "running" ? "open" : ""}>
              <summary>
                <span class="tool-state">${escapeHtml(stateLabel)}</span>
                <strong>${escapeHtml(receipt.label)}</strong>
                ${detail ? `<span>${escapeHtml(detail)}</span>` : ""}
              </summary>
              ${body ? `<pre>${escapeHtml(body)}</pre>` : ""}
            </details>
          `;
  }).join("")}
    </div>
  `;
}
function renderMarkdown(value) {
  const blocks = [];
  const lines = value.replace(/\r\n/g, "\n").split("\n");
  let paragraph = [];
  let list = [];
  let code = [];
  let inCode = false;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push(`<p>${renderInlineMarkdown(paragraph.join(" "))}</p>`);
    paragraph = [];
  };
  const flushList = () => {
    if (!list.length) return;
    blocks.push(`<ul>${list.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
    list = [];
  };
  const flushCode = () => {
    blocks.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
    code = [];
  };
  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        flushCode();
        inCode = false;
      } else {
        flushParagraph();
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      code.push(line);
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (!line.trim()) {
      flushParagraph();
      flushList();
    } else if (heading) {
      flushParagraph();
      flushList();
      const level = heading[1].length + 2;
      blocks.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
    } else if (bullet) {
      flushParagraph();
      list.push(bullet[1]);
    } else {
      flushList();
      paragraph.push(line.trim());
    }
  }
  if (inCode) flushCode();
  flushParagraph();
  flushList();
  return `<div class="markdown">${blocks.join("")}</div>`;
}
function renderInlineMarkdown(value) {
  return escapeHtml(value).replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>').replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\*([^*]+)\*/g, "<em>$1</em>");
}
function escapeHtml(value) {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

// ../../../gemstone/governor-interface/src/governor-ui/render.ts
var DEFAULT_ENDPOINT = "https://governor.influx.vision";
var CONTEXT_SLOT_COUNT = 15;
var STANDARD_TOOL_NAMES = /* @__PURE__ */ new Set([
  "add",
  "cat",
  "client_tools_mode",
  "divide",
  "file_grep",
  "file_read",
  "file_stat",
  "find_symbol",
  "find_tool",
  "git_diff",
  "git_log",
  "grep",
  "list_tools",
  "ls",
  "model_info",
  "modulo",
  "multiply",
  "neuron",
  "now",
  "patch_file",
  "power",
  "server_status",
  "sh",
  "shell",
  "shell_exec",
  "source_line",
  "status",
  "subtract",
  "tokenizer",
  "tool_help",
  "workspace_info",
  "write_file"
]);
var GOVERNOR_TOOL_NAMES = /* @__PURE__ */ new Set([
  "ask_oracle",
  "dispatch",
  "forge",
  "get_resources",
  "get_work_context",
  "kiro_delegate",
  "list_protocols",
  "list_tasks",
  "memory_audit",
  "model_load",
  "model_unload",
  "observe",
  "render_automation",
  "runtime_status",
  "schedule_work",
  "set_governor",
  "shadow_execute",
  "speculative_branch",
  "system_summary",
  "tool_manifest",
  "tool_manifest_promote",
  "topology_run",
  "topology_status",
  "write_insight"
]);
var GOVERNOR_TOOL_PREFIXES = [
  "apply_protocol",
  "asset_",
  "batch_",
  "capsule_",
  "context_",
  "cuda_",
  "embed_",
  "experiment_",
  "fleet_",
  "flux_",
  "gap_",
  "gemma_",
  "gpu_",
  "grid_",
  "healthy",
  "horse_",
  "mem_",
  "node_",
  "peer_",
  "render_",
  "run_task",
  "shard_",
  "spiral_",
  "stop_protocol",
  "stop_task",
  "surgical",
  "suture_",
  "task_",
  "visual_"
];
var GOVERNOR_TOOL_KEYWORDS = [
  "agentic",
  "autonomous",
  "capsule",
  "council",
  "daemon",
  "delegate",
  "fleet",
  "gemma",
  "grid",
  "memory",
  "nexus",
  "orchestration",
  "peer",
  "protocol",
  "scheduler",
  "suture",
  "workflow"
];
var anchor;
var inferenceProfile;
var backend;
var endpoint;
var model;
var systemPromptText;
var injectedContextText;
var selectedContextSlot;
var contextSlots;
var contextSlotDrafts;
var lastContextProofs;
var lastContextChars;
var lastContextTokens;
var contextWriterText;
var contextSearchText;
var contextEditing;
var memoryText;
var shardId;
var shardQuery;
var shardLimit;
var shardPath;
var shardPurpose;
var shardExtensions;
var connectionMachine;
var connectionR2Key;
var terminalCwd;
var terminalCommand;
var terminalStatus;
var terminalStarted;
var toolSearch;
var toolName;
var toolArgs;
var toolsEnabled;
var promptText;
var messages;
var discourseSeatId;
var councilLayout;
var councilSeats;
var statusText;
var modelInfo;
var busy;
var lastLatencyMs;
var lastRequestTokens;
var lastTokensPerSecond;
var lastCheckedAt;
var lastError;
var gemstoneBusy;
var gemstoneFields;
var selectedTarget;
var shardBusy;
var shardOutput;
var shardCommand;
var shardInventory;
var shardDetails;
var r2ShardRows;
var connectionBusy;
var connectionCommand;
var terminalBusy;
var contextBusy;
var contextSearchHydrating;
var contextLoaded;
var toolBusy;
var toolRows;
var toolOutput;
var toolCommand;
var daemonBusy;
var daemonOutput;
var daemonCommand;
var daemonLast;
var oscillihueBusy;
var oscillihueOutput;
var oscillihueCommand;
var oscillihueTasks;
var oscillihueTasksVisible;
var conversations;
var showConversationList;
function applyGovernorViewState(state2) {
  ({
    anchor,
    inferenceProfile,
    backend,
    endpoint,
    model,
    systemPromptText,
    injectedContextText,
    selectedContextSlot,
    contextSlots,
    contextSlotDrafts,
    lastContextProofs,
    lastContextChars,
    lastContextTokens,
    contextWriterText,
    contextSearchText,
    contextEditing,
    memoryText,
    shardId,
    shardQuery,
    shardLimit,
    shardPath,
    shardPurpose,
    shardExtensions,
    connectionMachine,
    connectionR2Key,
    terminalCwd,
    terminalCommand,
    terminalStatus,
    terminalStarted,
    toolSearch,
    toolName,
    toolArgs,
    toolsEnabled,
    promptText,
    discourseSeatId,
    messages,
    councilLayout,
    councilSeats,
    statusText,
    modelInfo,
    busy,
    lastLatencyMs,
    lastRequestTokens,
    lastTokensPerSecond,
    lastCheckedAt,
    lastError,
    gemstoneBusy,
    gemstoneFields,
    selectedTarget,
    shardBusy,
    shardOutput,
    shardCommand,
    shardInventory,
    shardDetails,
    r2ShardRows,
    connectionBusy,
    connectionCommand,
    terminalBusy,
    contextBusy,
    contextSearchHydrating,
    contextLoaded,
    toolBusy,
    toolRows,
    toolOutput,
    toolCommand,
    daemonBusy,
    daemonOutput,
    daemonCommand,
    daemonLast,
    oscillihueBusy,
    oscillihueOutput,
    oscillihueCommand,
    oscillihueTasks,
    oscillihueTasksVisible,
    conversations,
    showConversationList
  } = state2);
}
function renderGovernorApp(nextState) {
  applyGovernorViewState(nextState);
  return `
    <div class="shell">
      <aside class="rail">
        <div class="mark">
          <div class="mark-title">GOV</div>
          <div class="mark-sub">${escapeHtml(backendBadgeLabel())}</div>
          <div class="mark-sub">v${"motion-atlas-visionary"}</div>
        </div>
        <nav class="anchors" aria-label="Governor anchors">
          <div class="anchor-section-title">Work</div>
          ${anchorButton("chat", "Chat")}
          ${anchorButton("discourse", "Discourse")}
          ${anchorButton("council", "Council")}
          <div class="anchor-section-title">Memory</div>
          ${anchorButton("context", "Context")}
          ${anchorButton("memory", "Notes")}
          ${anchorButton("shards", "Shards")}
          <div class="anchor-section-title">Projects</div>
          ${anchorButton("oscillihue", "Oscillihue", true)}
          ${anchorButton("atelier", "Atelier")}
          <div class="anchor-section-title">Infra</div>
          ${anchorButton("tools", "Tools")}
          ${anchorButton("daemons", "Daemons")}
          ${anchorButton("connection", "Connection")}
          ${anchorButton("terminal", "Terminal")}
        </nav>
        <nav class="anchors anchors-bottom" aria-label="Governor help">
          ${anchorButton("usage", "Usage")}
        </nav>
      </aside>

      <main class="main">
        <section class="workspace">
          ${anchor === "chat" ? chatView() : ""}
          ${anchor === "discourse" ? discourseView() : ""}
          ${anchor === "council" ? councilView() : ""}
          ${anchor === "context" ? contextView() : ""}
          ${anchor === "memory" ? memoryView() : ""}
          ${anchor === "shards" ? shardsView() : ""}
          ${anchor === "tools" ? toolsView() : ""}
          ${anchor === "daemons" ? daemonsView() : ""}
          ${anchor === "oscillihue" ? oscillihueView() : ""}
          ${anchor === "atelier" ? atelierView() : ""}
          ${anchor === "connection" ? connectionStatusView() : ""}
          ${anchor === "terminal" ? terminalView() : ""}
          ${anchor === "usage" ? usageView() : ""}
        </section>
      </main>
    </div>
  `;
}
function anchorButton(id, label, project = false) {
  return governorAnchorButton(id, label, anchor === id, project);
}
function chatView() {
  const transcript = messages.length ? messages.map(messageView).join("") : `<div class="chat-empty">
        <div class="governor-orbit" aria-hidden="true">
          <i></i><i></i><i></i>
          <span class="orbit-core">G</span>
        </div>
        <span class="chat-empty-kicker">REASONING FIELD \xB7 READY</span>
        <h2>Bring the difficult thing.</h2>
        <p>Governor can reason across your context, call the right tools, and keep the operational thread intact.</p>
        <div class="starter-constellation" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div>
      </div>`;
  const action = busy ? `<button type="button" id="stop-chat">Stop</button>` : `<button type="submit">Send</button>`;
  const contextMetric = lastRequestTokens === null ? "ctx -" : `ctx ${formatInteger(lastRequestTokens)} tok`;
  const tpsMetric = lastTokensPerSecond === null ? "tps -" : `tps ${lastTokensPerSecond.toFixed(1)}`;
  const toolsMetric = toolsEnabled ? `tools ${toolRows.length || "on"}` : "tools off";
  const conversationListHtml = showConversationList ? `<div class="conversation-list">
        <div class="conversation-list-header">
          <strong>Saved conversations</strong>
          <button type="button" id="close-conversations" class="ghost">\xD7</button>
        </div>
        ${conversations.length === 0 ? `<div class="empty">No saved conversations.</div>` : ""}
        ${conversations.map((conv) => `
          <div class="conversation-row" data-conv-id="${escapeHtml(conv.id)}">
            <button type="button" class="conversation-load" data-conv-load="${escapeHtml(conv.id)}">
              <span class="conv-title">${escapeHtml(conv.title)}</span>
              <span class="conv-meta">${escapeHtml(conv.savedAt)} \xB7 ${conv.messageCount} msgs</span>
            </button>
            <button type="button" class="conversation-delete ghost" data-conv-delete="${escapeHtml(conv.id)}">\xD7</button>
          </div>
        `).join("")}
      </div>` : "";
  return `
    <div class="panel chat-panel">
      <div class="chat-atmosphere" aria-hidden="true"><i></i><i></i><i></i></div>
      <header class="chat-header">
        <div class="chat-title">
          <span class="chat-eyebrow">GOVERNOR \xB7 COGNITIVE INSTRUMENT</span>
          <h1>Reasoning, with gravity.</h1>
          <p>Context, memory, and tools held in one continuous field.</p>
        </div>
        <div class="chat-presence">
          <span class="presence-gem" aria-hidden="true"></span>
          <span><small>INFERENCE LANE</small><strong>${escapeHtml(backendBadgeLabel())}</strong></span>
        </div>
      </header>
      ${governorDatapoints()}
      ${activityStrip()}
      ${conversationListHtml}
      <div class="transcript" id="transcript">${transcript}</div>
      <form class="composer" id="composer">
        <div class="prompt-field">
          <span>DIRECTIVE</span>
          <textarea id="prompt" aria-label="Message Governor" placeholder="Ask, investigate, build, or direct\u2026" ${busy ? "disabled" : ""}>${escapeHtml(promptText)}</textarea>
          <i aria-hidden="true"></i>
        </div>
        <div class="composer-actions">
          <div class="chat-metrics">
            <span>${escapeHtml(contextMetric)}</span>
            <span>${escapeHtml(tpsMetric)}</span>
            <label class="tool-toggle">
              <input id="tools-enabled" type="checkbox" ${toolsEnabled ? "checked" : ""} ${busy ? "disabled" : ""}>
              <span>${escapeHtml(toolsMetric)}</span>
            </label>
          </div>
          <div class="button-row">
            <button type="button" id="new-conversation" class="ghost">New</button>
            <button type="button" id="save-conversation" class="ghost" ${messages.length === 0 ? "disabled" : ""}>Save</button>
            <button type="button" id="load-conversations" class="ghost">History</button>
            <button type="button" id="retry-chat" class="ghost" ${busy || !lastUserPrompt() ? "disabled" : ""}>Retry</button>
            ${action}
          </div>
        </div>
      </form>
    </div>
  `;
}
function discourseView() {
  const transcript = messages.length ? messages.map(messageView).join("") : `<div class="empty">Ready.</div>`;
  const action = busy ? `<button type="button" id="stop-chat">Stop</button>` : `<button type="submit">Send</button>`;
  const crossSeat = discourseCrossSeat();
  const seatOptions = councilSeats.map(
    (seat) => `<button type="button" class="${seat.id === discourseSeatId ? "active" : ""}" data-discourse-seat="${seat.id}">${escapeHtml(
      seat.name
    )}</button>`
  ).join("");
  return `
    <div class="panel discourse-panel">
      <section class="discourse-pane discourse-cross">
        <div class="discourse-cross-controls">
          <div class="discourse-cross-title">Side Agent</div>
          <div class="discourse-seat-selector">
            ${seatOptions}
          </div>
        </div>
        ${councilSeatView(crossSeat)}
      </section>

      <section class="discourse-pane discourse-gov">
        <div class="discourse-head">
          <h1>Gov (${escapeHtml(backendBadgeLabel())})</h1>
          <span data-live-activity>${escapeHtml(activityText())}</span>
        </div>
        <div class="transcript discourse-transcript" id="transcript">${transcript}</div>
        <form class="composer discourse-composer" id="composer">
          <textarea id="prompt" ${busy ? "disabled" : ""}>${escapeHtml(promptText)}</textarea>
          <div class="composer-actions">
            <div class="chat-metrics">
              <span>${escapeHtml(lastRequestTokens === null ? "ctx -" : `ctx ${formatInteger(lastRequestTokens)} tok`)}</span>
              <span>${escapeHtml(lastTokensPerSecond === null ? "tps -" : `tps ${lastTokensPerSecond.toFixed(1)}`)}</span>
              <label class="tool-toggle">
                <input id="tools-enabled" type="checkbox" ${toolsEnabled ? "checked" : ""} ${busy ? "disabled" : ""}>
                <span>${escapeHtml(toolsEnabled ? `tools ${toolRows.length || "on"}` : "tools off")}</span>
              </label>
            </div>
            <div class="button-row">
              <button type="button" id="clear-chat" class="ghost">Clear</button>
              <button type="button" id="retry-chat" class="ghost" ${busy || !lastUserPrompt() ? "disabled" : ""}>Retry</button>
              ${action}
            </div>
          </div>
        </form>
      </section>

      <section class="discourse-pane discourse-terminal">
        <div class="discourse-head">
          <h1>Terminal</h1>
          <span>${escapeHtml(terminalStatus || (terminalStarted ? "pty running" : "pty idle"))}</span>
        </div>
        <div id="terminal-screen" class="terminal-screen discourse-terminal-screen" aria-label="terminal" tabindex="0"></div>
        <div class="terminal-strip">
          <input id="terminal-cwd" value="${escapeHtml(terminalCwd)}" spellcheck="false" aria-label="terminal cwd">
          <div class="button-row">
            <button id="terminal-start" type="button" ${terminalBusy ? "disabled" : ""}>Start</button>
            <button id="terminal-ssh" type="button" ${terminalBusy ? "disabled" : ""}>SSH</button>
            <button id="terminal-reset" type="button" ${terminalBusy ? "disabled" : ""}>Reset</button>
          </div>
        </div>
      </section>
    </div>
  `;
}
function discourseCrossSeat() {
  return councilSeats.find((seat) => seat.id === discourseSeatId) ?? defaultDiscourseCrossSeat();
}
function defaultDiscourseCrossSeat() {
  return {
    id: discourseSeatId,
    name: `Seat ${discourseSeatId}`,
    messages: [],
    promptText: "",
    busy: false,
    lastLatencyMs: null,
    lastRequestTokens: null,
    lastCompletionTokens: null,
    lastTokensPerSecond: null,
    lastError: "-"
  };
}
function activityStrip() {
  const text = activityText();
  return `
    <div class="activity-strip ${busy ? "active" : ""}" aria-live="polite">
      <span class="activity-dot" aria-hidden="true"></span>
      <strong data-live-activity>${escapeHtml(text)}</strong>
    </div>
  `;
}
function activityText() {
  const value = (statusText || "").trim();
  if (!value || value === "-") return "Ready";
  return humanStatusText(value);
}
function councilView() {
  const visibleSeats = councilSeats.slice(0, councilLayout === "two" ? 3 : 5);
  const sharedMessages = visibleSeats[0]?.messages || [];
  const transcript = sharedMessages.length ? sharedMessages.map(messageView).join("") : `<div class="empty">Deliberation council ready. ${visibleSeats.length} members present.</div>`;
  const anyBusy = visibleSeats.some((s) => s.busy);
  const firstSeat = visibleSeats[0];
  const status = anyBusy ? "deliberating" : firstSeat?.lastError && firstSeat.lastError !== "-" ? firstSeat.lastError : "";
  const seatNames = visibleSeats.map((s) => escapeHtml(s.name)).join(", ");
  return `
    <div class="panel council-panel deliberation">
      <section class="copy inline council-head">
        <div>
          <h1>Council</h1>
          <p>Deliberation between ${visibleSeats.length} members. Each responds when relevant.</p>
        </div>
        <div class="segmented" role="group" aria-label="Council size">
          <button type="button" data-council-layout="two" class="${councilLayout === "two" ? "active" : ""}">3</button>
          <button type="button" data-council-layout="four" class="${councilLayout === "four" ? "active" : ""}">5</button>
        </div>
      </section>
      <section class="council-members">
        ${visibleSeats.map((s) => `
          <div class="council-member">
            <input class="council-seat-name" data-council-name="${s.id}" value="${escapeHtml(s.name)}" spellcheck="false" aria-label="member ${s.id} name">
          </div>
        `).join("")}
      </section>
      <div class="transcript council-transcript" data-council-transcript="1">${transcript}</div>
      <form class="composer council-composer" data-council-form="${firstSeat?.id || 1}">
        <textarea data-council-prompt="${firstSeat?.id || 1}" ${anyBusy ? "disabled" : ""}>${escapeHtml(firstSeat?.promptText || "")}</textarea>
        <div class="composer-actions">
          <div class="chat-metrics">${status ? `<span>${escapeHtml(status)}</span>` : `<span>${seatNames}</span>`}</div>
          <div class="button-row">
            <button type="button" data-council-clear="${firstSeat?.id || 1}" class="ghost" ${anyBusy ? "disabled" : ""}>Clear</button>
            <button type="submit" ${anyBusy ? "disabled" : ""}>${anyBusy ? "Deliberating" : "Send"}</button>
          </div>
        </div>
      </form>
    </div>
  `;
}
function councilSeatView(seat) {
  const transcript = seat.messages.length ? seat.messages.map(messageView).join("") : `<div class="empty">Seat ${seat.id} ready.</div>`;
  const status = seat.busy ? "sending" : seat.lastError && seat.lastError !== "-" ? seat.lastError : "";
  return `
    <article class="council-seat" data-council-seat="${seat.id}">
      <header class="council-seat-head">
        <input class="council-seat-name" data-council-name="${seat.id}" value="${escapeHtml(seat.name)}" spellcheck="false" aria-label="seat ${seat.id} name">
      </header>
      <div class="transcript council-transcript" data-council-transcript="${seat.id}">${transcript}</div>
      <form class="composer council-composer" data-council-form="${seat.id}">
        <textarea data-council-prompt="${seat.id}" ${seat.busy ? "disabled" : ""}>${escapeHtml(seat.promptText)}</textarea>
        <div class="composer-actions">
          <div class="chat-metrics">${status ? `<span>${escapeHtml(status)}</span>` : ""}</div>
          <div class="button-row">
            <button type="button" data-council-clear="${seat.id}" class="ghost" ${seat.busy ? "disabled" : ""}>Clear</button>
            <button type="submit" ${seat.busy ? "disabled" : ""}>${seat.busy ? "Sending" : "Send"}</button>
          </div>
        </div>
      </form>
    </article>
  `;
}
function governorDatapoints() {
  const picked = pickedContextCount();
  const promptLabel = systemPromptLinkLabel();
  const shardCount = memoryShardCount();
  const latencyLabel = lastLatencyMs === null ? "-" : `${lastLatencyMs}ms`;
  const tokensLabel = lastTokensPerSecond === null ? "-" : `${lastTokensPerSecond.toFixed(1)}/s`;
  return `
    <div class="datapoints" aria-label="Governor datapoints">
      <button class="datapoint static backend-datapoint" type="button" disabled title="Current backend">
        <span>${escapeHtml(backendBadgeLabel())}</span>
      </button>
      <button class="datapoint" type="button" data-anchor-link="tools" title="Open tools">
        ${iconTools()}
        <span>${escapeHtml(String(toolRows.length))}</span>
      </button>
      <button class="datapoint" type="button" data-anchor-link="context" title="Open context slots">
        ${iconPaper()}
        <span>${escapeHtml(`${picked}/${CONTEXT_SLOT_COUNT}`)}</span>
      </button>
      <button class="datapoint" type="button" data-anchor-link="memory" title="Open prompt">
        ${iconSystem()}
        <span>${escapeHtml(promptLabel)}</span>
      </button>
      <button class="datapoint static" type="button" disabled title="Last request latency">
        ${iconLatency()}
        <span>${escapeHtml(latencyLabel)}</span>
      </button>
      <button class="datapoint static" type="button" disabled title="Last tokens per second">
        ${iconTokens()}
        <span>${escapeHtml(tokensLabel)}</span>
      </button>
      ${shardCount > 0 ? `<button class="datapoint" type="button" data-anchor-link="shards" title="Open memory shards">
              ${iconShard()}
              <span>${escapeHtml(String(shardCount))}</span>
            </button>` : ""}
    </div>
  `;
}
function messageView(message) {
  return governorMessageView(message);
}
function contextView() {
  const slot = currentContextSlot();
  const draft = contextSlotDrafts[slot.slot];
  const title = draft?.title ?? slot.title;
  const content = draft?.content ?? contextWriterText;
  const ordered = orderedContextSlots();
  const filtered = filterContextSlots(ordered);
  const search = contextSearchText.trim();
  const picked = pickedContextCount();
  return `
    <div class="panel context-picker" id="context-panel">
      <div class="context-sidebar">
        <div class="context-search">
          <input id="context-search" value="${escapeHtml(contextSearchText)}" spellcheck="false" placeholder="Search" aria-label="search context">
        </div>
        <div class="context-slots">
          ${filtered.length ? filtered.map((row) => {
    const rowState = [
      row.slot === selectedContextSlot ? "active" : "",
      row.picked ? "picked" : ""
    ].filter(Boolean).join(" ");
    return `
                      <button class="context-slot ${rowState}" data-context-slot="${row.slot}" type="button">
                        <span>${String(row.slot).padStart(2, "0")}</span>
                        <strong>${escapeHtml(contextSlotDrafts[row.slot]?.title ?? row.title)}</strong>
                        <em>${row.picked ? String(row.order || 1).padStart(2, "0") : ""}</em>
                      </button>
                    `;
  }).join("") : `<div class="context-empty">No matches.</div>`}
        </div>
        <div class="context-sidebar-footer">
          <span>${escapeHtml(`${picked}/${CONTEXT_SLOT_COUNT}`)}</span>
          <button id="context-add" type="button" ${contextBusy ? "disabled" : ""}>+</button>
          <button id="context-sync-pull" type="button" ${contextBusy ? "disabled" : ""}>Pull</button>
          <button id="context-sync-push" type="button" ${contextBusy ? "disabled" : ""}>Push</button>
        </div>
      </div>
      <div class="context-editor">
        <div class="context-title-bar">
          <input id="context-writer-name" value="${escapeHtml(title)}" spellcheck="false" placeholder="Title" aria-label="context title">
          <button id="context-toggle" type="button" class="${slot.picked ? "active" : ""}" ${contextBusy ? "disabled" : ""}>${slot.picked ? "On" : "Off"}</button>
          <button id="context-up" type="button" ${contextBusy || !slot.picked ? "disabled" : ""}>\u2191</button>
          <button id="context-down" type="button" ${contextBusy || !slot.picked ? "disabled" : ""}>\u2193</button>
        </div>
        <textarea id="context-writer-text" class="context-body" spellcheck="false" placeholder="Write context here...">${escapeHtml(content)}</textarea>
      </div>
      ${contextProofView()}
    </div>
  `;
}
function contextProofView() {
  const picked = contextSlots.filter((slot) => slot.picked).length;
  const rows = lastContextProofs.length ? lastContextProofs : orderedContextSlots().filter((slot) => slot.picked).map((slot) => ({
    slot: slot.slot,
    key: slot.key,
    title: slot.title,
    path: slot.path,
    order: slot.order,
    chars: slot.chars,
    tokens: estimateTokensFromChars(slot.chars),
    sha256: ""
  }));
  return `
    <aside class="context-proof" aria-label="context insertion proof">
      <div class="context-proof-head">
        <strong>Context Proof</strong>
        <span>${escapeHtml(`${picked}/${CONTEXT_SLOT_COUNT} picked`)}</span>
        <span>${escapeHtml(`${formatInteger(lastContextChars || rows.reduce((sum, row) => sum + row.chars, 0))} chars`)}</span>
        <span>${escapeHtml(`${formatInteger(lastContextTokens || rows.reduce((sum, row) => sum + row.tokens, 0))} est tok`)}</span>
      </div>
      <div class="context-proof-list">
        ${rows.length ? rows.map(
    (row) => `
                    <div class="context-proof-row">
                      <span>${escapeHtml(String(row.order || row.slot).padStart(2, "0"))}</span>
                      <strong>${escapeHtml(row.title || row.key)}</strong>
                      <em>${escapeHtml(`${formatInteger(row.chars)} chars / ${formatInteger(row.tokens)} tok`)}</em>
                      <code>${escapeHtml(row.sha256 ? row.sha256.slice(0, 12) : "not injected yet")}</code>
                      <small>${escapeHtml(row.path || row.key)}</small>
                    </div>
                  `
  ).join("") : `<div class="empty">No picked context.</div>`}
      </div>
    </aside>
  `;
}
function memoryView() {
  return `
    <div class="panel split compact-panel">
      <div class="copy inline">
        <h1>Memory</h1>
        <p>Operator notes appended to the system prompt.</p>
        <button id="save-memory" type="button">Save</button>
      </div>
      <textarea id="memory-text" class="field tall" placeholder="Memory notes...">${escapeHtml(memoryText)}</textarea>
    </div>
  `;
}
function shardsView() {
  const defaultR2Key = shardId ? `shards/${shardId}.shard` : "";
  const r2KeyValue = connectionR2Key || defaultR2Key;
  return `
    <div class="panel shards-panel">
      <div class="shard-controls">
        <label>
          <span>Shard / Handle</span>
          <input id="shard-id" value="${escapeHtml(shardId)}" spellcheck="false">
        </label>
        <label>
          <span>Query</span>
          <input id="shard-query" value="${escapeHtml(shardQuery)}" spellcheck="false">
        </label>
        <label>
          <span>Path</span>
          <input id="shard-path" value="${escapeHtml(shardPath)}" spellcheck="false">
        </label>
        <label class="short-field">
          <span>Limit</span>
          <input id="shard-limit" value="${escapeHtml(String(shardLimit))}" inputmode="numeric" spellcheck="false">
        </label>
        <label>
          <span>Purpose</span>
          <input id="shard-purpose" value="${escapeHtml(shardPurpose)}" spellcheck="false">
        </label>
        <label>
          <span>Extensions</span>
          <input id="shard-extensions" value="${escapeHtml(shardExtensions)}" spellcheck="false">
        </label>
        <label>
          <span>R2 Key</span>
          <input id="connection-r2-key" value="${escapeHtml(r2KeyValue)}" spellcheck="false">
        </label>
        <div class="button-row">
          <button id="shard-build" type="button" ${shardBusy ? "disabled" : ""}>Build</button>
          <button id="shard-create" type="button" ${shardBusy ? "disabled" : ""}>Create</button>
          <button id="shard-ingest" type="button" ${shardBusy ? "disabled" : ""}>Ingest</button>
          <button id="shard-status" type="button" ${shardBusy ? "disabled" : ""}>Status</button>
          <button id="shard-locate" type="button" ${shardBusy ? "disabled" : ""}>Locate</button>
          <button id="shard-inspect" type="button" ${shardBusy ? "disabled" : ""}>Inspect</button>
          <button id="shard-query-run" type="button" ${shardBusy ? "disabled" : ""}>Query</button>
          <button id="shard-pack" type="button" ${shardBusy ? "disabled" : ""}>Pack</button>
          <button id="connection-r2-inventory" type="button" ${connectionBusy ? "disabled" : ""}>List R2</button>
          <button id="connection-r2-pull" type="button" ${connectionBusy ? "disabled" : ""}>Shard &lt;- R2</button>
          <button id="connection-r2-push" type="button" ${connectionBusy ? "disabled" : ""}>Shard -> R2</button>
        </div>
      </div>
      <div class="shard-protocol">
        <section>
          <strong>mmap shard</strong>
          <span>Create, ingest, query, inspect, and pack disk-backed shard files.</span>
        </section>
        <section>
          <strong>CUDA byte vault</strong>
          <span>Pin shard bytes into GPU memory, then read bounded snippets back into prompt context.</span>
          <div>
            <button id="cuda-status" type="button" ${shardBusy ? "disabled" : ""}>CUDA Status</button>
            <button id="cuda-pin" type="button" ${shardBusy ? "disabled" : ""}>Pin</button>
            <button id="cuda-read" type="button" ${shardBusy ? "disabled" : ""}>Read</button>
          </div>
        </section>
      </div>
      <div class="shard-readable">
        ${shardInventoryView()}
        ${r2ShardInventoryView()}
        ${shardDetailsView()}
      </div>
      <details class="raw-output">
        <summary>Raw command output</summary>
        <pre class="cli-output shard-output">${escapeHtml(shardOutput || "No raw output yet.")}</pre>
      </details>
      <div class="row">
        <div class="mono small">${escapeHtml(shardCommand)}</div>
        <div class="mono small">Council mmap shards \xB7 CUDA byte vault</div>
      </div>
    </div>
  `;
}
function toolsView() {
  return `
    <div class="panel tools-panel">
      <div class="tool-controls">
        <label>
          <span>Search</span>
          <input id="tool-search" value="${escapeHtml(toolSearch)}" spellcheck="false">
        </label>
        <label>
          <span>Selected Tool</span>
          <input id="tool-name" value="${escapeHtml(toolName)}" spellcheck="false">
        </label>
        <label>
          <span>JSON Args</span>
          <input id="tool-args" value="${escapeHtml(toolArgs)}" spellcheck="false">
        </label>
        <div class="button-row">
          <button id="tool-list" type="button" ${toolBusy ? "disabled" : ""}>List</button>
          <button id="tool-help" type="button" ${toolBusy ? "disabled" : ""}>Help</button>
          <button id="tool-call" type="button" ${toolBusy ? "disabled" : ""}>Call</button>
        </div>
      </div>
      <section class="tool-readable">${toolListView()}</section>
      <details class="raw-output" open>
        <summary>Tool output</summary>
        <pre class="cli-output shard-output">${escapeHtml(toolOutput || "No tool output yet.")}</pre>
      </details>
      <div class="row">
        <div class="mono small">${escapeHtml(toolCommand)}</div>
        <div class="mono small">${escapeHtml(toolRows.length ? `${toolRows.length} Governor tools` : "Governor tools")}</div>
      </div>
    </div>
  `;
}
function daemonsView() {
  const machineValue = connectionMachine || "gem";
  return `
    <div class="panel daemons-panel">
      <section class="copy inline daemon-head">
        <div>
          <h1>Daemons</h1>
          <p>Service daemons for long-running automation and asset workflows.</p>
        </div>
        <label class="daemon-machine">
          <span>Machine</span>
          <input id="daemon-machine" value="${escapeHtml(machineValue)}" spellcheck="false">
        </label>
      </section>
      <section class="daemons-split">
        <div class="daemons-half">
          <div class="daemons-half-head">
            <h2>Suture</h2>
            <span>systemd service bridge</span>
          </div>
          ${daemonCard("suture", "suture")}
        </div>
        <div class="daemons-half">
          <div class="daemons-half-head">
            <h2>Nexus</h2>
            <span>asset workflow daemon</span>
          </div>
          ${daemonCard("nexus", "nexus")}
          ${daemonCard("piper", "nexus")}
        </div>
      </section>
      <pre class="cli-output daemon-output">${escapeHtml(daemonOutput || "No daemon command yet.")}</pre>
      <div class="row">
        <div class="mono small">${escapeHtml(daemonCommand)}</div>
        <div class="mono small">${escapeHtml(daemonBusy ? "running" : "ready")}</div>
      </div>
    </div>
  `;
}
function oscillihueView() {
  const groups = [
    [
      "Runtime",
      [
        ["status", "Status", "repo/database/provider readiness"],
        ["setup", "Setup", "install Python requirements"],
        ["serve", "Serve", "open terminal with python app.py"],
        ["compile", "Compile", "py_compile service modules"]
      ]
    ],
    [
      "Users",
      [
        ["users", "Auth Tests", "passwordless, sessions, credits"],
        ["billing", "Billing Tests", "entitlements and render tiers"]
      ]
    ],
    [
      "Resources",
      [
        ["resources", "Storage Tests", "object storage, R2, janitor"],
        ["qa", "Local QA", "preflight for browser QA"]
      ]
    ],
    [
      "Security",
      [
        ["security", "Security Tests", "tenant, upload, edge, preset safety"],
        ["deploy", "Edge Validate", "Caddy production contract"]
      ]
    ]
  ];
  return `
    <div class="panel oscillihue-panel">
      <div class="oscillihue-orbital" aria-hidden="true">
        <span></span><span></span><span></span><span></span><span></span>
      </div>
      <section class="oscillihue-grid">
        <div class="oscillihue-status">
          <div class="oscillihue-wordmark">
            <span>OSCILLIHUE</span>
            <strong>Command Center</strong>
          </div>
          <div class="oscillihue-facts">
            ${oscillihueFact("root", "~/Oscillihue")}
            ${oscillihueFact("service", "FastAPI :8445")}
            ${oscillihueFact("auth", "magic link / Google OIDC")}
            ${oscillihueFact("billing", "Stripe ledger")}
            ${oscillihueFact("storage", "local + R2")}
            ${oscillihueFact("edge", "Caddy / oscillihue.com")}
          </div>
        </div>
        <div class="oscillihue-controls">
          ${groups.map(
    ([group, rows]) => `
                <section class="oscillihue-group">
                  <h2>${escapeHtml(group)}</h2>
                  <div>
                    ${rows.map(
      ([kind, label, command]) => `
                          <button type="button" data-oscillihue-command="${escapeHtml(kind)}" ${oscillihueBusy ? "disabled" : ""}>
                            <strong>${escapeHtml(label)}</strong>
                            <span>${escapeHtml(command)}</span>
                          </button>
                        `
    ).join("")}
                  </div>
                </section>
              `
  ).join("")}
        </div>
      </section>
      ${oscillihueTaskListView()}
      <section class="oscillihue-console">
        <div>
          <strong>${escapeHtml(oscillihueBusy ? "running" : oscillihueCommand || "ready")}</strong>
          <span>${escapeHtml(oscillihueBusy ? "Oscillihue command active" : "local checkout")}</span>
        </div>
        <pre>${escapeHtml(oscillihueOutput || "No Oscillihue command output yet.")}</pre>
      </section>
    </div>
  `;
}
function oscillihueTaskListView() {
  const doneCount = oscillihueTasks.filter((t) => t.done).length;
  const totalCount = oscillihueTasks.length;
  return `
    <section class="oscillihue-tasks">
      <button id="oscillihue-tasks-toggle" type="button" class="oscillihue-tasks-btn ${oscillihueTasksVisible ? "active" : ""}">
        <strong>Launch Checklist</strong>
        <span>${totalCount > 0 ? `${doneCount}/${totalCount}` : "load"}</span>
      </button>
      ${oscillihueTasksVisible ? `
        <div class="oscillihue-task-list">
          ${oscillihueTasks.length === 0 ? `<div class="empty">Loading...</div>` : oscillihueTasks.map((task) => `
            <button class="oscillihue-task ${task.done ? "done" : ""}" data-oscillihue-task="${task.id}" type="button">
              <span class="oscillihue-task-check">${task.done ? "\u2713" : ""}</span>
              <span class="oscillihue-task-name">${escapeHtml(task.name)}</span>
            </button>
          `).join("")}
        </div>
      ` : ""}
    </section>
  `;
}
function oscillihueFact(label, value) {
  return `
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}
function atelierView() {
  return `
    <div class="panel atelier-panel">
      <div class="atelier-header">
        <h1>Atelier</h1>
        <p>Creative workspace and render pipeline interface.</p>
      </div>
      <div class="atelier-stub">
        <div class="empty">Atelier integration coming soon.</div>
      </div>
    </div>
  `;
}
function daemonCard(daemon, focus) {
  const labels = {
    suture: {
      title: "Suture",
      body: "Local stitched-runtime bridge. Status checks the suture_dispatch daemon process directly.",
      command: "local process"
    },
    nexus: {
      title: "Nexus",
      body: "Asset workflow daemon for planning, queueing, and coordinating generated media jobs.",
      command: "gemstone nexus"
    },
    piper: {
      title: "Piper",
      body: "Runtime worker daemon that carries pipeline work for Nexus-backed generation flows.",
      command: "gemstone piper"
    }
  };
  const last = daemonLast[daemon];
  const active = daemon === focus ? " active" : "";
  return `
    <article class="daemon-card${active}">
      <header>
        <div>
          <h2>${escapeHtml(labels[daemon].title)}</h2>
          <span>${escapeHtml(labels[daemon].command)}</span>
        </div>
        <strong>${escapeHtml(last ? last.ok ? "ok" : "failed" : "-")}</strong>
      </header>
      <p>${escapeHtml(labels[daemon].body)}</p>
      <div class="daemon-actions">
        ${daemonActionButton(daemon, "status", "Status")}
        ${daemonActionButton(daemon, "check", "Check")}
        ${daemonActionButton(daemon, "start", "Start")}
        ${daemonActionButton(daemon, "stop", "Stop")}
        ${daemonActionButton(daemon, "restart", "Restart")}
        ${daemonActionButton(daemon, "logs", "Logs")}
        ${daemon !== "suture" ? daemonActionButton(daemon, "setup", "Setup") : ""}
      </div>
    </article>
  `;
}
function daemonActionButton(daemon, action, label) {
  return `<button type="button" data-daemon="${daemon}" data-daemon-action="${action}" ${daemonBusy ? "disabled" : ""}>${label}</button>`;
}
function connectionStatusView() {
  const endpointUrl = safeUrl(endpoint);
  const cliEndpoint = gemstoneFields.endpoint || "";
  const displayedModel = profileBoundModel(model, modelInfo?.id, backend);
  const resolvedModel = modelInfo?.id || "-";
  const root2 = modelInfo?.root || gemstoneFields.model || "-";
  const context = modelInfo?.max_model_len ? String(modelInfo.max_model_len) : gemstoneFields.context || "-";
  const latency = lastLatencyMs === null ? "-" : `${lastLatencyMs}ms`;
  const gpu = [
    gemstoneFields.tp ? `tp ${gemstoneFields.tp}` : "",
    gemstoneFields["gpu-util"] ? `gpu ${gemstoneFields["gpu-util"]}` : "",
    gemstoneFields.speculative ? `spec ${gemstoneFields.speculative}` : ""
  ].filter(Boolean).join(" \xB7 ") || "-";
  const cliEndpointButton = cliEndpoint && !isLoopbackEndpoint(cliEndpoint) ? `<button id="use-cli-endpoint" type="button" class="ghost">Use CLI</button>` : "";
  const shardValue = shardId || "council-source";
  const defaultR2Key = shardValue ? `shards/${shardValue}.shard` : "";
  const r2KeyValue = connectionR2Key || defaultR2Key;
  const runtimeLabel = resolveRuntimeLabel();
  const statusRows = [
    ["runtime", runtimeLabel],
    ["machine", connectionMachine || "-"],
    ["endpoint", gemstoneFields.endpoint || compactEndpoint(endpoint)],
    ["configured", displayedModel],
    ["resolved", resolvedModel],
    ["target", selectedTarget],
    ["root", root2],
    ["image", gemstoneFields.image || "-"],
    ["gpu", gpu],
    ["context", context],
    ["cache", gemstoneFields["cache-key"] || "-"],
    ["latency", latency],
    ["used", lastRequestTokens === null ? "-" : `${formatInteger(lastRequestTokens)} tok`],
    ["tps", lastTokensPerSecond === null ? "-" : lastTokensPerSecond.toFixed(1)],
    ["checked", lastCheckedAt],
    ["error", lastError]
  ];
  return `
    <div class="panel connection-status-panel">
      <div class="connection-status-controls">
        <label>
          <span>Machine</span>
          <input id="status-machine-input" value="${escapeHtml(connectionMachine)}" spellcheck="false">
        </label>
        <label>
          <span>Runtime</span>
          <select id="backend-select">
            <option value="remote" ${backend === "remote" ? "selected" : ""}>Remote</option>
            <option value="mlx" ${backend === "mlx" ? "selected" : ""}>Local</option>
          </select>
        </label>
        <label>
          <span>URL</span>
          <input id="endpoint-input" value="${escapeHtml(compactEndpoint(endpoint))}" spellcheck="false">
        </label>
        <label>
          <span>Model</span>
          <input id="model-input" value="${escapeHtml(model)}" spellcheck="false">
        </label>
        <label>
          <span>Shard</span>
          <input id="connection-shard-id" value="${escapeHtml(shardValue)}" spellcheck="false">
        </label>
        <label>
          <span>R2 Key</span>
          <input id="connection-r2-key" value="${escapeHtml(r2KeyValue)}" spellcheck="false">
        </label>
        <div class="button-row">
          <button id="check-status" type="button" ${busy ? "disabled" : ""}>Check</button>
          <button id="check-gemstone" type="button" ${gemstoneBusy ? "disabled" : ""}>Gemstone</button>
          <button id="connection-status" type="button" ${connectionBusy ? "disabled" : ""}>Status</button>
          <button id="connection-stop" type="button" ${connectionBusy ? "disabled" : ""}>Stop</button>
          <button id="connection-start" type="button" ${connectionBusy ? "disabled" : ""}>Start</button>
          <button id="connection-provision" type="button" ${connectionBusy ? "disabled" : ""}>A100</button>
          <button id="connection-r2-check" type="button" ${connectionBusy ? "disabled" : ""}>R2</button>
          <button id="update-source" type="button" ${terminalBusy ? "disabled" : ""}>Update</button>
          ${cliEndpointButton}
        </div>
      </div>
      ${governorStatusLines(statusRows)}
      <div class="row">
        <div class="mono small">${escapeHtml(statusText || connectionCommand)}</div>
        <div class="mono small">${escapeHtml(endpointUrl ? "openai-compatible" : "-")}</div>
      </div>
    </div>
  `;
}
function resolveRuntimeLabel() {
  const ep = endpoint || "";
  if (backend === "remote" && !isLoopbackEndpoint(ep)) return "Remote";
  if (isLoopbackEndpoint(ep)) {
    const image = (gemstoneFields.image || "").toLowerCase();
    if (image.includes("llama.cpp") || image.includes("llama-cpp") || image.includes("ggml")) return "llama.cpp (local GPU)";
    if (image.includes("vllm")) return "vLLM (local GPU)";
    if (image.includes("tgi") || image.includes("text-generation")) return "TGI (local GPU)";
    return "Local inference";
  }
  if (backend === "mlx") return "Local inference";
  return backendLabel(backend);
}
function r2ShardInventoryView() {
  const rows = r2ShardRows;
  const objects = rows.filter((row) => !row.prefix);
  const prefixes = rows.length - objects.length;
  const loaded = objects.filter((row) => row.loaded).length;
  return `
    <section class="r2-shard-section">
      <div class="section-title">
        <span>R2 Shards</span>
        <em>${escapeHtml(rows.length ? `${objects.length} objects \xB7 ${prefixes} prefixes \xB7 ${loaded} loaded` : "not listed")}</em>
      </div>
      ${rows.length ? `<div class="r2-shard-list">
              ${rows.map((row) => {
    const active = row.key === connectionR2Key || row.shard_id === shardId ? "active" : "";
    const loadedClass = row.prefix ? "prefix" : row.loaded ? "loaded" : "remote-only";
    const state2 = row.prefix ? "prefix" : row.loaded && row.index_loaded ? "loaded" : row.loaded ? "shard file" : row.indexed ? "manifest only" : "remote only";
    return `
                    <button class="r2-shard-row ${active} ${loadedClass}" data-r2-shard-select="${escapeHtml(row.key)}" data-r2-shard-id="${escapeHtml(row.shard_id)}" type="button" title="${escapeHtml(row.local_path || row.key)}">
                      <span>${escapeHtml(row.shard_id)}</span>
                      <strong>${escapeHtml(row.key)}</strong>
                      <time>${escapeHtml(row.updated_at)}</time>
                      <em>${escapeHtml(row.size === null ? "-" : formatBytes(row.size))}</em>
                      <b>${escapeHtml(state2)}</b>
                    </button>
                  `;
  }).join("")}
            </div>` : `<div class="shard-empty">Press R2 Shards to list remote shard objects. This does not pull or restore anything.</div>`}
    </section>
  `;
}
function terminalView() {
  return `
    <div class="panel terminal-panel">
      <div class="terminal-controls">
        <label>
          <span>CWD</span>
          <input id="terminal-cwd" value="${escapeHtml(terminalCwd)}" spellcheck="false">
        </label>
        <label>
          <span>Command</span>
          <input id="terminal-command" value="${escapeHtml(terminalCommand)}" spellcheck="false">
        </label>
        <div class="button-row">
          <button id="terminal-start" type="button" ${terminalBusy ? "disabled" : ""}>Start</button>
          <button id="terminal-run" type="button" ${terminalBusy ? "disabled" : ""}>Run</button>
          <button id="terminal-open" type="button" ${terminalBusy ? "disabled" : ""}>Open</button>
          <button id="terminal-ssh" type="button" ${terminalBusy ? "disabled" : ""}>SSH</button>
          <button id="terminal-a100" type="button" ${terminalBusy ? "disabled" : ""}>A100 Load</button>
          <button id="terminal-reset" type="button" ${terminalBusy ? "disabled" : ""}>Reset</button>
        </div>
      </div>
      <div id="terminal-screen" class="terminal-screen" aria-label="terminal"></div>
      <div class="row">
        <div class="mono small">${escapeHtml(terminalStatus || (terminalStarted ? "pty running" : "pty idle"))}</div>
        <div class="mono small">${escapeHtml(connectionMachine ? `machine ${connectionMachine}` : "local shell")}</div>
      </div>
    </div>
  `;
}
function toolListView() {
  if (!toolRows.length) {
    return `<div class="tool-empty">Press List to load Governor's live tools.</div>`;
  }
  const partition = partitionToolRows(toolRows);
  return `
    <div class="tool-partitions" aria-label="Governor tools">
      ${toolPartitionView("standard", "Standard Tools", "Direct utility and discovery calls.", partition.standard)}
      ${toolPartitionView("governor", "Governor Tools", "Agentic, orchestration, fleet, memory, workflow, Grid, Suture, Nexus, and peer-control tools exposed through Gemstone.", partition.governor)}
    </div>
  `;
}
function toolPartitionView(kind, title, description, rows) {
  return `
    <section class="tool-partition ${kind}">
      <div class="tool-partition-head">
        <div>
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(description)}</p>
        </div>
        <span>${escapeHtml(String(rows.length))}</span>
      </div>
      ${rows.length ? `<div class="tool-list">${rows.map((row) => toolItemView(row, kind)).join("")}</div>` : `<div class="tool-empty compact">No matching tools in this partition.</div>`}
    </section>
  `;
}
function toolItemView(tool, kind) {
  const active = tool.name === toolName ? "active" : "";
  return `
    <button class="tool-item ${active}" data-tool-select="${escapeHtml(tool.name)}" type="button">
      <span>${escapeHtml(tool.name)}</span>
      ${kind === "governor" ? `<strong>${escapeHtml(tool.description || "-")}</strong>` : ""}
    </button>
  `;
}
function partitionToolRows(rows) {
  return rows.reduce(
    (partition, row) => {
      const target = isGovernorTool(row) ? partition.governor : partition.standard;
      target.push(row);
      return partition;
    },
    { standard: [], governor: [] }
  );
}
function isGovernorTool(tool) {
  const name = tool.name.toLowerCase();
  const description = tool.description.toLowerCase();
  if (STANDARD_TOOL_NAMES.has(name)) return false;
  if (GOVERNOR_TOOL_NAMES.has(name)) return true;
  if (GOVERNOR_TOOL_PREFIXES.some((prefix) => name.startsWith(prefix))) return true;
  return GOVERNOR_TOOL_KEYWORDS.some((keyword) => description.includes(keyword));
}
function shardInventoryView() {
  const entries = Object.entries(shardInventory?.shards || {}).sort(([a], [b]) => a.localeCompare(b));
  if (!entries.length) {
    return `
      <section class="shard-section">
        <div class="section-title">Loaded Shards</div>
        <div class="shard-empty">No local shards are loaded. Pull one from R2 or create and ingest a new shard.</div>
      </section>
    `;
  }
  return `
    <section class="shard-section">
      <div class="section-title">
        <span>Loaded Shards</span>
        <em>${escapeHtml(String(entries.length))} local</em>
      </div>
      <div class="shard-list" aria-label="Available shards">
        ${entries.map(([id, info]) => {
    const active = id === shardId ? "active" : "";
    return `
              <button class="shard-item ${active}" data-shard-select="${escapeHtml(id)}" type="button">
                <span class="shard-name">${escapeHtml(id)}</span>
                <span class="shard-purpose">${escapeHtml(info.purpose || "No purpose recorded")}</span>
                <span class="shard-meta">${escapeHtml(formatBytes(info.byte_size || 0))}</span>
                <span class="shard-meta">${escapeHtml(String(info.block_count || 0))} blocks</span>
              </button>
            `;
  }).join("")}
      </div>
    </section>
  `;
}
function shardDetailsView() {
  const info = shardDetails?.info;
  if (!info) return "";
  const sources = (info.sources || []).slice(0, 8);
  const blocks = (shardDetails?.blocks || []).slice(0, 12);
  return `
    <section class="shard-section shard-detail">
      <div class="section-title">
        <span>Selected Shard</span>
        <em>${escapeHtml(info.shard_id || shardId)}</em>
      </div>
      <div class="detail-grid">
        <div><span>Purpose</span><strong>${escapeHtml(info.purpose || "No purpose recorded")}</strong></div>
        <div><span>Size</span><strong>${escapeHtml(formatBytes(info.byte_size || 0))}</strong></div>
        <div><span>Blocks</span><strong>${escapeHtml(String(info.block_count || blocks.length || 0))}</strong></div>
        <div><span>Shard File</span><strong>${escapeHtml(shardDetails?.shard || "-")}</strong></div>
        <div><span>Index File</span><strong>${escapeHtml(shardDetails?.index || "-")}</strong></div>
      </div>
      ${sources.length ? `<div class="source-list">
            <div class="subhead">Sources</div>
            ${sources.map((source) => `<div>${escapeHtml(source)}</div>`).join("")}
          </div>` : ""}
      ${blocks.length ? `<div class="block-table">
            <div class="block-header">
              <span>Block</span>
              <span>Bytes</span>
              <strong>Source</strong>
            </div>
            ${blocks.map(
    (block) => `
                  <div>
                    <span>${escapeHtml(block.block_id || "-")}</span>
                    <span>${escapeHtml(String(block.length ?? "-"))}</span>
                    <strong>${escapeHtml(block.source_path || "-")}</strong>
                  </div>
                `
  ).join("")}
          </div>` : ""}
    </section>
  `;
}
function usageView() {
  const rows = [
    ["Chat", "Ask Governor for plans, code-reading, command strategy, operational checks, and server work. Use direct requests with paths, machine names, expected outputs, and constraints."],
    ["Discourse", "Run two chat streams plus a live PTY terminal in one split view for agent cross-checking and tooling."],
    ["Council", "Run two or four independent conversations against the same endpoint for comparison, role split, or parallel reasoning."],
    ["Context", "Store durable operating notes, project facts, architecture decisions, and current objectives. Picked slots are injected into the system prompt."],
    ["Memory", "Keep operator notes that should shape every answer. This is the fastest place to remind Governor about preferences, standing rules, or active work."],
    ["Shards", "Create, ingest, locate, inspect, pack, and query memory shards. Use shards for larger codebases or reference material that should survive beyond one chat."],
    ["Tools", "List available Governor tools and call a named tool with JSON arguments. Use this when the model should inspect or operate through Gemstone rather than guess."],
    ["Connection", "Check, start, provision, and restore Governor on the configured machine. This is the operational surface for remote GPU/runtime work."],
    ["Terminal", "Run local shell commands from the app or open an embedded terminal. Use it for quick checks, builds, status commands, and direct Gemstone CLI work."],
    ["Status", "Verify endpoint, model, target machine, cache, image, context length, latency, token use, and server readiness."]
  ];
  return `
    <div class="panel usage-panel">
      <div class="usage-map">
        ${rows.map(
    ([name, detail]) => `
              <button type="button" class="usage-row" data-anchor-link="${escapeHtml(name.toLowerCase())}">
                <strong>${escapeHtml(name)}</strong>
                <span>${escapeHtml(detail)}</span>
              </button>
            `
  ).join("")}
      </div>
    </div>
  `;
}
function currentContextSlot() {
  return contextSlots.find((slot) => slot.slot === selectedContextSlot) || contextSlots[0] || {
    slot: selectedContextSlot,
    key: `context_${selectedContextSlot}`,
    title: `CONTEXT ${selectedContextSlot}`,
    path: "",
    picked: false,
    order: 0,
    chars: 0
  };
}
function backendLabel(value) {
  return value === "mlx" ? "Local" : "Remote";
}
function backendBadgeLabel() {
  return backend === "mlx" ? "Local" : "Remote";
}
function humanStatusText(value) {
  const elapsed = value.match(/(\d+s)$/)?.[1] || "";
  const clean = value.replace(/\.+\s+\d+s$/, "").trim();
  const suffix = elapsed ? ` (${elapsed})` : "";
  const lower = clean.toLowerCase();
  if (lower === "connecting") return `Opening the model connection${suffix}`;
  if (lower === "loading context") return "Loading picked context slots";
  if (lower === "building request") return "Building the prompt for the model";
  if (lower === "model request") return `Waiting for the model to answer${suffix}`;
  if (lower.startsWith("tool round")) return `${clean}${suffix}`;
  if (lower.startsWith("tool ") && lower.endsWith(" running")) return `${clean}${suffix}`;
  if (lower.includes("retrying no tools")) return "Tool call timed out; retrying without tools";
  if (lower === "checking") return `Checking the configured endpoint${suffix}`;
  if (lower === "refreshing") return "Refreshing endpoint and Gemstone status";
  if (lower === "ready") return "Ready";
  if (lower === "error") return "The last request failed";
  if (lower === "stopped") return "Request stopped";
  return `${clean}${suffix}`;
}
function profileBoundModel(value, served, backend2) {
  const trimmed = value.trim();
  if (!trimmed && backend2 === "mlx") return "default_model";
  if (backend2 === "mlx" && isLocalAliasModel(trimmed)) return trimmed;
  return served || trimmed || "-";
}
function isLocalAliasModel(value) {
  return ["default_model", "governor", "gemma", "council"].includes(value.trim().toLowerCase());
}
function orderedContextSlots() {
  const slots = [...contextSlots];
  return slots.sort((a, b) => {
    if (a.picked && b.picked) return (a.order || 99) - (b.order || 99);
    if (a.picked !== b.picked) return a.picked ? -1 : 1;
    return a.slot - b.slot;
  });
}
function filterContextSlots(slots) {
  const terms = contextSearchTerms();
  if (!terms.length) return slots;
  return slots.filter((slot) => {
    const body = contextSlotSearchBody(slot);
    return terms.every((term) => body.includes(term));
  });
}
function contextSearchTerms() {
  return contextSearchText.toLowerCase().split(/\s+/).map((term) => term.trim()).filter(Boolean);
}
function contextSlotSearchBody(slot) {
  const draft = contextSlotDrafts[slot.slot];
  return [slot.slot, slot.key, draft?.title ?? slot.title, slot.path, draft?.content ?? slot.content ?? ""].join("\n").toLowerCase();
}
function formatInteger(value) {
  return new Intl.NumberFormat().format(Math.round(value));
}
function estimateTokensFromChars(chars) {
  if (!Number.isFinite(chars) || chars <= 0) return 0;
  return Math.max(1, Math.ceil(chars / 4));
}
function pickedContextCount() {
  return contextSlots.filter((slot) => slot.picked).length;
}
function memoryShardCount() {
  return Object.keys(shardInventory?.shards || {}).length;
}
function systemPromptLinkLabel() {
  return [systemPromptText, injectedContextText, memoryText].some((value) => value.trim()) ? "prompt" : "prompt -";
}
function iconTools() {
  return `
    <svg class="datapoint-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M14.7 6.3a4.2 4.2 0 0 0 5 5L12 19l-5-5 7.7-7.7Z"></path>
      <path d="m5 16-2 2 3 3 2-2"></path>
    </svg>
  `;
}
function iconPaper() {
  return `
    <svg class="datapoint-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M7 3h7l4 4v14H7V3Z"></path>
      <path d="M14 3v5h5"></path>
      <path d="M9.5 12h7"></path>
      <path d="M9.5 16h5"></path>
    </svg>
  `;
}
function iconSystem() {
  return `
    <svg class="datapoint-icon" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="4" y="5" width="16" height="12" rx="1.5"></rect>
      <path d="M9 21h6"></path>
      <path d="M12 17v4"></path>
      <path d="m9 10 2 2-2 2"></path>
      <path d="M13 14h3"></path>
    </svg>
  `;
}
function iconLatency() {
  return `
    <svg class="datapoint-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 8v5l3 2"></path>
      <circle cx="12" cy="13" r="8"></circle>
      <path d="M9 2h6"></path>
    </svg>
  `;
}
function iconTokens() {
  return `
    <svg class="datapoint-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4 7h16"></path>
      <path d="M4 12h10"></path>
      <path d="M4 17h7"></path>
      <path d="m16 14 4 3-4 3"></path>
    </svg>
  `;
}
function iconShard() {
  return `
    <svg class="datapoint-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 3 4.5 7.2 12 11.4l7.5-4.2L12 3Z"></path>
      <path d="M4.5 11.5 12 15.7l7.5-4.2"></path>
      <path d="M4.5 15.8 12 20l7.5-4.2"></path>
    </svg>
  `;
}
function formatBytes(value) {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB"];
  let next = value;
  let unit = 0;
  while (next >= 1024 && unit < units.length - 1) {
    next /= 1024;
    unit += 1;
  }
  return `${next >= 10 || unit === 0 ? next.toFixed(0) : next.toFixed(1)} ${units[unit]}`;
}
function safeUrl(value) {
  try {
    return new URL(normalizeEndpoint(value));
  } catch {
    return null;
  }
}
function compactEndpoint(value) {
  try {
    const url = new URL(normalizeEndpoint(value));
    return `${url.host}${url.pathname.replace(/\/v1\/?$/, "")}`.replace(/\/$/, "");
  } catch {
    return value.replace(/^https?:\/\//, "").replace(/\/v1\/?$/, "");
  }
}
function normalizeEndpoint(value, fallback = DEFAULT_ENDPOINT) {
  let next = value.trim().replace(/\/+$/, "");
  if (!next) next = fallback;
  if (!/^https?:\/\//i.test(next)) {
    next = isBareLoopback(next) ? `http://${next}` : `https://${next}`;
  }
  return next.endsWith("/v1") ? next : `${next}/v1`;
}
function isLoopbackEndpoint(value) {
  try {
    const url = new URL(normalizeEndpoint(value));
    return isLoopbackHost(url.hostname);
  } catch {
    return isBareLoopback(value);
  }
}
function isBareLoopback(value) {
  return /^(https?:\/\/)?(127\.0\.0\.1|localhost|\[::1\]|::1)(:\d+)?(\/|$)/i.test(value.trim());
}
function isLoopbackHost(hostname) {
  return ["127.0.0.1", "localhost", "::1", "[::1]"].includes(hostname);
}
function lastUserPrompt() {
  const index = findLastMessageIndex(messages, "user");
  return index === -1 ? "" : messages[index].content;
}
function findLastMessageIndex(source, role) {
  for (let index = source.length - 1; index >= 0; index -= 1) {
    if (source[index].role === role) return index;
  }
  return -1;
}

// visionary.ts
var root = document.querySelector("#app");
if (!root) throw new Error("missing #app");
var storageKey = "motion-atlas:visionary-messages:v1";
var messages2 = JSON.parse(localStorage.getItem(storageKey) || "[]");
var promptText2 = "";
var attachments = [];
var busy2 = false;
var statusText2 = "Visionary ready";
var lastError2 = "-";
var lastLatencyMs2 = null;
var abortController = null;
var emptyOutput = { ok: false, code: null, stdout: "", stderr: "", executable: "", duration_ms: 0 };
var textMessages = () => messages2.map((message) => ({
  role: message.role,
  content: message.attachmentCount ? `${message.content}
[${message.attachmentCount} multimodal attachment(s)]` : message.content,
  at: message.at
}));
var state = () => ({
  anchor: "chat",
  backend: "remote",
  inferenceProfile: "standard",
  endpoint: window.location.origin,
  model: "coolthor/gemma-4-12B-it-FP8-dynamic",
  systemPromptText: "You are Visionary, a precise multimodal Gemma 4 assistant. Inspect the supplied media carefully and state what is visible before reasoning.",
  injectedContextText: "",
  contextSource: "",
  contextWriterName: "",
  contextWriterText: "",
  contextSearchText: "",
  contextEditing: false,
  selectedContextSlot: 1,
  contextSlots: [],
  contextSlotDrafts: {},
  lastContextProofs: [],
  lastContextChars: 0,
  lastContextTokens: 0,
  memoryText: "",
  shardId: "",
  shardQuery: "",
  shardLimit: 8,
  shardPath: "",
  shardPurpose: "",
  shardExtensions: "",
  connectionMachine: "",
  connectionR2Key: "",
  terminalCwd: "",
  terminalCommand: "",
  terminalStatus: "",
  terminalStarted: false,
  toolSearch: "",
  toolName: "",
  toolArgs: "{}",
  toolsEnabled: false,
  promptText: promptText2,
  discourseSeatId: 1,
  messages: textMessages(),
  councilLayout: "two",
  councilSeats: [],
  statusText: statusText2,
  modelInfo: null,
  lastLatencyMs: lastLatencyMs2,
  lastRequestTokens: null,
  lastCompletionTokens: null,
  lastTokensPerSecond: null,
  lastCheckedAt: "-",
  lastError: lastError2,
  gemstoneStatus: emptyOutput,
  gemstoneList: emptyOutput,
  gemstoneFields: {},
  selectedTarget: "Visionary",
  shardOutput: "",
  shardCommand: "",
  shardLoaded: false,
  shardInventory: null,
  shardDetails: null,
  r2ShardRows: [],
  r2ShardInventoryOutput: "",
  connectionOutput: "",
  connectionCommand: "",
  contextLoaded: false,
  contextOutput: "",
  contextCommand: "",
  toolLoaded: false,
  toolRows: [],
  toolOutput: "",
  toolCommand: "",
  daemonOutput: "",
  daemonCommand: "",
  daemonLast: { suture: null, nexus: null, piper: null },
  oscillihueOutput: "",
  oscillihueCommand: "",
  oscillihueTasks: [],
  oscillihueTasksVisible: false,
  conversations: [],
  showConversationList: false,
  busy: busy2,
  gemstoneBusy: false,
  shardBusy: false,
  connectionBusy: false,
  terminalBusy: false,
  contextBusy: false,
  contextSearchHydrating: false,
  toolBusy: false,
  daemonBusy: false,
  oscillihueBusy: false
});
var now = () => (/* @__PURE__ */ new Date()).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
var persist = () => localStorage.setItem(storageKey, JSON.stringify(messages2));
function render() {
  root.innerHTML = renderGovernorApp(state());
  document.querySelectorAll("[data-anchor]").forEach((node) => {
    if (node.dataset.anchor !== "chat") node.hidden = true;
  });
  const form = document.querySelector("#composer");
  if (form && !form.querySelector(".visionaryControls")) {
    form.insertAdjacentHTML("afterbegin", `<div class="visionaryControls"><label class="visionaryAttach">ADD MEDIA<input id="visionaryFiles" type="file" accept="image/*,audio/*,video/*" multiple></label><span id="visionaryMediaStatus">TEXT ONLY \xB7 IMAGE / AUDIO / VIDEO READY</span><button type="button" id="visionaryReset">RESET CHAT</button></div><div id="visionaryPreview" class="visionaryPreview"></div>`);
  }
  document.querySelector("#visionaryReset")?.addEventListener("click", () => {
    messages2 = [];
    attachments = [];
    persist();
    render();
  });
  document.querySelector("#new-conversation")?.addEventListener("click", () => {
    messages2 = [];
    attachments = [];
    persist();
    render();
  });
  document.querySelector("#stop-chat")?.addEventListener("click", () => abortController?.abort());
  const prompt = document.querySelector("#prompt");
  prompt?.addEventListener("input", () => {
    promptText2 = prompt.value;
  });
  prompt?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      void send(prompt.value);
    }
  });
  document.querySelector("#composer")?.addEventListener("submit", (event) => {
    event.preventDefault();
    void send(prompt?.value || "");
  });
  document.querySelector("#visionaryFiles")?.addEventListener("change", (event) => {
    void readFiles(event.target.files);
  });
  updatePreview();
  document.querySelector("#transcript")?.scrollTo({ top: 1e6 });
}
async function readFiles(files) {
  if (!files) return;
  attachments = await Promise.all(Array.from(files).slice(0, 4).map((file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, mime: file.type, data: String(reader.result) });
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  })));
  updatePreview();
}
function updatePreview() {
  const node = document.querySelector("#visionaryPreview");
  if (!node) return;
  node.innerHTML = attachments.map((file) => file.mime.startsWith("image/") ? `<img src="${file.data}" alt="${file.name}"><span>${file.name}</span>` : `<span class="visionaryFile">${file.mime.split("/")[0].toUpperCase()} \xB7 ${file.name}</span>`).join("");
  const status = document.querySelector("#visionaryMediaStatus");
  if (status) status.textContent = attachments.length ? `${attachments.length} MEDIA ATTACHMENT(S) READY` : "TEXT ONLY \xB7 IMAGE / AUDIO / VIDEO READY";
}
function apiContent(text, files) {
  const content = [{ type: "text", text: text || "Inspect the attached media." }];
  for (const file of files) {
    if (file.mime.startsWith("image/")) content.push({ type: "image_url", image_url: { url: file.data } });
    else if (file.mime.startsWith("video/")) content.push({ type: "video_url", video_url: { url: file.data } });
    else if (file.mime.startsWith("audio/")) content.push({ type: "input_audio", input_audio: { data: file.data.split(",")[1] || file.data, format: file.mime.split("/")[1] || "wav" } });
  }
  return content;
}
async function send(raw) {
  const text = raw.trim();
  if (!text && !attachments.length || busy2) return;
  const sentFiles = attachments;
  attachments = [];
  const started = performance.now();
  const user = { role: "user", content: text, at: now(), attachmentCount: sentFiles.length };
  messages2 = [...messages2, user, { role: "assistant", content: "", at: now() }];
  promptText2 = "";
  busy2 = true;
  statusText2 = "Visionary is reading";
  lastError2 = "-";
  persist();
  render();
  abortController = new AbortController();
  try {
    const history = messages2.slice(0, -1).map((message) => ({ role: message.role, content: message.content }));
    history.push({ role: "user", content: apiContent(text, sentFiles) });
    const response = await fetch("/api/visionary/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model: "governor", messages: history, temperature: 0.2, max_tokens: 4096, stream: false }), signal: abortController.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Visionary returned HTTP ${response.status}`);
    messages2[messages2.length - 1].content = data.choices?.[0]?.message?.content || "[empty response]";
    lastLatencyMs2 = Math.round(performance.now() - started);
    const elapsedSec = lastLatencyMs2 / 1000;
    const completionTokens = data.usage?.completion_tokens;
    if (completionTokens && elapsedSec > 0) {
      lastTokensPerSecond = completionTokens / elapsedSec;
      lastRequestTokens = data.usage?.total_tokens || null;
    }
    statusText2 = "Visionary ready";
  } catch (error) {
    const stopped = error instanceof DOMException && error.name === "AbortError";
    const message = stopped ? "stopped" : error instanceof Error ? error.message : String(error);
    messages2[messages2.length - 1].content = `[${message}]`;
    lastError2 = message;
    statusText2 = stopped ? "Request stopped" : "Visionary unavailable";
  } finally {
    busy2 = false;
    abortController = null;
    persist();
    render();
  }
}
render();
