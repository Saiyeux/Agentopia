import { Activity, Database, FileText, KeyRound, Play, RefreshCw, Save, ScrollText, ServerCog, Sparkles, Trash2, Users } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AppliedDelta,
  Character,
  LlmModel,
  LlmProvider,
  LlmSettings,
  PromptDocument,
  PromptMeta,
  SceneLog,
  WorldState,
  fetchLlmModels,
  fetchLlmSettings,
  fetchPrompt,
  fetchPrompts,
  fetchCharacters,
  fetchSceneLog,
  fetchWorld,
  runOneStep,
  clearSceneLog,
  resetCharacters,
  resetWorld,
  resetPrompt,
  savePrompt,
  startOpening,
  saveLlmSettings,
  testLlmConnection
} from "./api";
import "./styles.css";

type TabKey = "world" | "characters" | "logs" | "prompts" | "api";

type LoadState = {
  world: WorldState | null;
  characters: Character[];
  logs: SceneLog[];
  error: string | null;
  loading: boolean;
  running: boolean;
  autoRunning: boolean;
};

const initialState: LoadState = {
  world: null,
  characters: [],
  logs: [],
  error: null,
  loading: true,
  running: false,
  autoRunning: false
};

function formatValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.length ? value.join(" / ") : "[]";
  }
  if (value && typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value ?? "");
}

function App() {
  const [state, setState] = useState<LoadState>(initialState);
  const [activeTab, setActiveTab] = useState<TabKey>("world");
  const autoRunningRef = useRef(false);
  const steppingRef = useRef(false);
  const actorNames = Object.fromEntries(state.characters.map((character) => [character.id, character.name]));

  async function load() {
    setState((current) => ({ ...current, loading: true }));
    try {
      const [world, characters, logs] = await Promise.all([
        fetchWorld(),
        fetchCharacters(),
        fetchSceneLog()
      ]);
      setState((current) => ({ ...current, world, characters, logs, loading: false }));
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "加载失败",
        loading: false
      }));
    }
  }

  function clearError() {
    setState((current) => ({ ...current, error: null }));
  }

  async function step(options: { switchToLogs?: boolean } = {}) {
    if (steppingRef.current) {
      return;
    }
    steppingRef.current = true;
    setState((current) => ({ ...current, running: true, error: null }));
    try {
      await runOneStep();
      await load();
      if (options.switchToLogs) {
        setActiveTab("logs");
      }
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "运行失败",
        running: false
      }));
      steppingRef.current = false;
      return;
    }
    setState((current) => ({ ...current, running: false }));
    steppingRef.current = false;
  }

  async function clearLogs() {
    setAutoRunning(false);
    setState((current) => ({ ...current, running: true, error: null }));
    try {
      await clearSceneLog();
      await load();
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "清理失败"
      }));
    }
    setState((current) => ({ ...current, running: false }));
  }

  async function opening() {
    setState((current) => ({ ...current, running: true, error: null }));
    try {
      await startOpening();
      await load();
      setActiveTab("logs");
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "开场失败"
      }));
    }
    setState((current) => ({ ...current, running: false }));
  }

  async function resetCharacterState() {
    setAutoRunning(false);
    setState((current) => ({ ...current, running: true, error: null }));
    try {
      await resetCharacters();
      await load();
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "还原角色失败"
      }));
    }
    setState((current) => ({ ...current, running: false }));
  }

  async function resetWholeWorld() {
    setAutoRunning(false);
    if (!window.confirm("确定要完全重置世界吗？这将删除所有旧角色和日志，重新加载赛博朋克角色。")) {
      return;
    }
    setState((current) => ({ ...current, running: true, error: null }));
    try {
      await resetWorld();
      await load();
      setActiveTab("characters");
    } catch (error) {
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "重置世界失败"
      }));
    }
    setState((current) => ({ ...current, running: false }));
  }

  function setAutoRunning(next: boolean) {
    autoRunningRef.current = next;
    setState((current) => ({ ...current, autoRunning: next }));
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!state.autoRunning) {
      return;
    }
    let cancelled = false;

    async function loop() {
      while (!cancelled && autoRunningRef.current) {
        await step();
        await new Promise((resolve) => window.setTimeout(resolve, 1200));
      }
    }

    void loop();
    return () => {
      cancelled = true;
    };
  }, [state.autoRunning]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>Agentopia</h1>
          <p>自主角色世界模拟器</p>
        </div>
        <button className="icon-button" onClick={() => void load()} title="刷新">
          <RefreshCw size={18} />
        </button>
      </header>

      {state.error ? (
        <div className="error">
          <div style={{ flex: 1 }}>
            <strong>错误：</strong>
            <pre style={{ margin: "8px 0 0", whiteSpace: "pre-wrap", fontSize: "12px", lineHeight: 1.4 }}>
              {state.error}
            </pre>
          </div>
          <button
            onClick={clearError}
            style={{
              background: "transparent",
              border: "1px solid #e4b49e",
              borderRadius: "4px",
              color: "#7b2d15",
              cursor: "pointer",
              padding: "4px 12px",
              fontSize: "12px"
            }}
          >
            关闭
          </button>
        </div>
      ) : null}

      <nav className="tabs" aria-label="主视图">
        <TabButton
          active={activeTab === "world"}
          icon={<Activity size={17} />}
          label="世界"
          onClick={() => setActiveTab("world")}
        />
        <TabButton
          active={activeTab === "characters"}
          icon={<Users size={17} />}
          label="角色"
          onClick={() => setActiveTab("characters")}
        />
        <TabButton
          active={activeTab === "logs"}
          icon={<ScrollText size={17} />}
          label={`事件 ${state.world?.log_count ?? state.logs.length}`}
          onClick={() => setActiveTab("logs")}
        />
        <TabButton
          active={activeTab === "prompts"}
          icon={<FileText size={17} />}
          label="提示词"
          onClick={() => setActiveTab("prompts")}
        />
        <TabButton
          active={activeTab === "api"}
          icon={<ServerCog size={17} />}
          label="API"
          onClick={() => setActiveTab("api")}
        />
      </nav>

      <section className="dashboard">
        {activeTab === "world" ? (
          <WorldPanel world={state.world} loading={state.loading} />
        ) : null}
        {activeTab === "characters" ? (
          <CharactersPanel
            characters={state.characters}
            running={state.running}
            onReset={() => void resetCharacterState()}
            onResetWorld={() => void resetWholeWorld()}
          />
        ) : null}
        {activeTab === "logs" ? (
          <LogPanel
            actorNames={actorNames}
            autoRunning={state.autoRunning}
            logs={state.logs}
            running={state.running}
            onClear={() => void clearLogs()}
            onOpening={() => void opening()}
            onStep={() => void step({ switchToLogs: false })}
            onToggleAuto={() => setAutoRunning(!state.autoRunning)}
          />
        ) : null}
        {activeTab === "prompts" ? <PromptPanel /> : null}
        {activeTab === "api" ? <ApiPanel /> : null}
      </section>
    </main>
  );
}

function TabButton({
  active,
  icon,
  label,
  onClick
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button className={active ? "tab-button active" : "tab-button"} onClick={onClick} type="button">
      {icon}
      <span>{label}</span>
    </button>
  );
}

function WorldPanel({
  world,
  loading
}: {
  world: WorldState | null;
  loading: boolean;
}) {
  return (
    <section className="panel world-panel">
      <div className="panel-title">
        <div>
          <Activity size={18} />
          <h2>世界状态</h2>
        </div>
      </div>
      {world ? (
        <>
          <div className="metric-grid">
            <Metric label="Tick" value={world.sim_tick} />
            <Metric label="Day" value={world.day} />
            <Metric label="Chapter" value={world.chapter} />
            <Metric label="Period" value={world.period} />
            <Metric label="Weather" value={world.weather} />
            <Metric label="Tension" value={world.tension} />
            <Metric label="Economy" value={world.economy_index} />
          </div>
          <p className="worldview">{world.worldview}</p>
          {world.current_scene ? (
            <div className="scene-strip">
              <span>当前场景</span>
              <strong>{world.current_scene.title || world.current_scene.location_id}</strong>
              <small>
                #{world.current_scene.id} · {world.current_scene.turn_count}/{world.current_scene.turn_budget} ·{" "}
                {world.current_scene.participants.length} 人
              </small>
            </div>
          ) : null}
          <div className="threads">
            <Sparkles size={16} />
            <span>{world.active_threads.length} 条剧情线</span>
          </div>
        </>
      ) : (
        <p className="muted">{loading ? "正在连接后端..." : "暂无世界数据"}</p>
      )}
    </section>
  );
}

function CharactersPanel({
  characters,
  running,
  onReset,
  onResetWorld
}: {
  characters: Character[];
  running: boolean;
  onReset: () => void;
  onResetWorld: () => void;
}) {
  return (
    <section className="panel characters-panel">
      <div className="panel-title">
        <div>
          <Users size={18} />
          <h2>角色</h2>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="mode-button" disabled={running} onClick={onReset} type="button">
            还原角色
          </button>
          <button className="danger-button" disabled={running} onClick={onResetWorld} type="button">
            完全重置
          </button>
        </div>
      </div>
      <div className="character-list">
        {characters.map((character) => (
          <article className="character-card" key={character.id}>
            <div className="character-heading">
              <div>
                <h3>{character.name}</h3>
              </div>
              <Database size={16} />
            </div>
            <p>{character.summary}</p>
            <div className="kv-list">
              {Object.entries(character.attributes).map(([id, attr]) => (
                <div key={id}>
                  <span>{attr.label}</span>
                  <strong>{formatValue(attr.value)}</strong>
                </div>
              ))}
            </div>
            <div className="trait-list">
              {Object.entries(character.traits).map(([id, trait]) => (
                <div className="trait-row" key={id}>
                  <span>{trait.label}</span>
                  <div className="trait-bar" aria-label={`${trait.label} ${trait.score}`}>
                    <i style={{ width: `${trait.score}%` }} />
                  </div>
                  <strong>{trait.score}</strong>
                </div>
              ))}
            </div>
            <div className="relationship-list">
              <h4>角色关系</h4>
              {character.relationships.length ? (
                character.relationships.map((relationship) => (
                  <div className="relationship-row" key={relationship.to_id}>
                    <strong>{relationship.name}</strong>
                    <span>
                      熟悉 {relationship.familiarity} · 好感 {relationship.affection} · 信任 {relationship.trust} · 尊重 {relationship.respect}
                    </span>
                  </div>
                ))
              ) : (
                <small>尚未直接认识其他角色</small>
              )}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function LogPanel({
  actorNames,
  autoRunning,
  logs,
  running,
  onClear,
  onOpening,
  onStep,
  onToggleAuto
}: {
  actorNames: Record<string, string>;
  autoRunning: boolean;
  logs: SceneLog[];
  running: boolean;
  onClear: () => void;
  onOpening: () => void;
  onStep: () => void;
  onToggleAuto: () => void;
}) {
  const endRef = useRef<HTMLDivElement | null>(null);
  const displayLogs = attachVerdicts(logs);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [logs.length]);

  return (
    <section className="panel log-panel">
      <div className="log-toolbar">
        <div className="panel-title">
          <div>
            <ScrollText size={18} />
            <h2>事件流</h2>
          </div>
        </div>
        <div className="run-controls">
          <div className="control-group primary-controls">
            <button className="primary-action" disabled={running} onClick={onStep} type="button">
              <Play size={16} />
              <span>{running ? "运行中" : "一拍"}</span>
            </button>
            <button
              className={autoRunning ? "mode-button active" : "mode-button"}
              onClick={onToggleAuto}
              type="button"
            >
              {autoRunning ? "停止" : "自动"}
            </button>
          </div>
          <div className="control-group utility-controls">
            <button className="mode-button" disabled={running} onClick={onOpening} type="button">
              首句
            </button>
            <button className="danger-button" disabled={running} onClick={onClear} type="button">
              <Trash2 size={15} />
              <span>清理</span>
            </button>
          </div>
        </div>
      </div>
      <div className="log-list">
        {displayLogs.map(({ log, verdict }) => (
          <article className={`log-entry log-entry-${log.type}`} key={log.id}>
            <div className="log-meta">
              <span className="log-type">{log.type}</span>
              <span>tick {log.tick}</span>
              <span>{displayActorName(log.actor_id, actorNames)}</span>
            </div>
            <p className="log-content">{log.content}</p>
            {verdict ? <VerdictResult verdict={verdict} actorNames={actorNames} /> : null}
          </article>
        ))}
        <div ref={endRef} />
      </div>
    </section>
  );
}

function attachVerdicts(logs: SceneLog[]): Array<{ log: SceneLog; verdict: SceneLog | null }> {
  const items: Array<{ log: SceneLog; verdict: SceneLog | null }> = [];
  for (const log of logs) {
    if (log.type === "verdict") {
      const previous = items[items.length - 1];
      if (
        previous &&
        previous.log.type === "speech" &&
        previous.log.tick === log.tick &&
        previous.log.scene_id === log.scene_id
      ) {
        previous.verdict = log;
        continue;
      }
    }
    items.push({ log, verdict: null });
  }
  return items;
}

function VerdictResult({
  verdict,
  actorNames
}: {
  verdict: SceneLog;
  actorNames: Record<string, string>;
}) {
  const applied = Array.isArray(verdict.data.applied) ? (verdict.data.applied as AppliedDelta[]) : [];
  const changed = applied.filter((item) => item.status === "applied" && item.old !== item.new);
  return (
    <div className="verdict-result">
      <span className="verdict-label">结果</span>
      {changed.length ? (
        <ul>
          {changed.map((item, index) => (
            <li key={`${item.ref}-${item.field}-${index}`}>
              {deltaOwner(item, actorNames)} {fieldLabel(item.field)} {formatDelta(item)}
            </li>
          ))}
        </ul>
      ) : (
        <small>无明显属性变化</small>
      )}
    </div>
  );
}

const providerDefaults: Record<LlmProvider, string> = {
  lmstudio: "http://127.0.0.1:1234/v1",
  ollama: "http://127.0.0.1:11434",
  api: "https://api.openai.com/v1"
};

const providerLabels: Record<LlmProvider, string> = {
  lmstudio: "LM Studio",
  ollama: "Ollama",
  api: "API"
};

function ApiPanel() {
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [provider, setProvider] = useState<LlmProvider>("lmstudio");
  const [baseUrl, setBaseUrl] = useState(providerDefaults.lmstudio);
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [models, setModels] = useState<LlmModel[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadSettings() {
    setBusy(true);
    setError(null);
    try {
      const current = await fetchLlmSettings();
      applySettings(current);
      setMessage(current.api_key_set ? "已加载配置，密钥已保存。" : "已加载配置。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取 API 配置失败");
    }
    setBusy(false);
  }

  function applySettings(next: LlmSettings) {
    setSettings(next);
    setProvider(next.provider);
    setBaseUrl(next.base_url);
    setModel(next.model);
    setApiKey("");
  }

  function chooseProvider(next: LlmProvider) {
    setProvider(next);
    setBaseUrl(providerDefaults[next]);
    setModels([]);
    if (next !== provider) {
      setMessage(null);
      setError(null);
    }
  }

  async function loadModels() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const currentModel = model || settings?.model || "qwen-agentworld-35b-a3b";
      await saveLlmSettings({ provider, base_url: baseUrl, model: currentModel, api_key: apiKey });
      const result = await fetchLlmModels();
      setModels(result.models);
      applySettings(result.settings);
      if (!model && result.models[0]?.id) {
        setModel(result.models[0].id);
      }
      setMessage(`已拉取 ${result.models.length} 个模型。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "拉取模型失败");
    }
    setBusy(false);
  }

  async function saveConfig() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await saveLlmSettings({ provider, base_url: baseUrl, model, api_key: apiKey });
      applySettings(result.settings);
      setMessage("API 配置已保存，下一拍会使用这组配置。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    }
    setBusy(false);
  }

  async function testConfig() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await saveLlmSettings({ provider, base_url: baseUrl, model, api_key: apiKey });
      const result = await testLlmConnection();
      setMessage(`连接正常，可见模型 ${String(result.model_count ?? 0)} 个。`);
      const current = await fetchLlmSettings();
      applySettings(current);
    } catch (err) {
      setError(err instanceof Error ? err.message : "测试失败");
    }
    setBusy(false);
  }

  useEffect(() => {
    void loadSettings();
  }, []);

  return (
    <section className="panel api-panel">
      <div className="panel-title">
        <div>
          <ServerCog size={18} />
          <h2>API 配置</h2>
        </div>
      </div>

      <div className="settings-form">
        <div className="provider-control" role="tablist" aria-label="模型服务商">
          {(Object.keys(providerLabels) as LlmProvider[]).map((item) => (
            <button
              className={provider === item ? "provider-button active" : "provider-button"}
              key={item}
              onClick={() => chooseProvider(item)}
              type="button"
            >
              {providerLabels[item]}
            </button>
          ))}
        </div>

        <label className="field">
          <span>API 地址</span>
          <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
        </label>

        <label className="field">
          <span>API 密钥</span>
          <div className="input-with-icon">
            <KeyRound size={16} />
            <input
              autoComplete="off"
              placeholder={settings?.api_key_set ? "已保存，留空表示不修改" : "本地服务可留空"}
              type="password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </div>
        </label>

        <label className="field">
          <span>模型</span>
          <div className="model-row">
            <select value={model} onChange={(event) => setModel(event.target.value)}>
              {model ? <option value={model}>{model}</option> : <option value="">先拉取模型</option>}
              {models
                .filter((item) => item.id !== model)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name ?? item.id}
                  </option>
                ))}
            </select>
            <button className="mode-button" disabled={busy} onClick={() => void loadModels()} type="button">
              拉模型
            </button>
          </div>
        </label>

        <div className="settings-actions">
          <button className="primary-action" disabled={busy} onClick={() => void saveConfig()} type="button">
            <Save size={16} />
            <span>保存</span>
          </button>
          <button className="mode-button" disabled={busy} onClick={() => void testConfig()} type="button">
            测试
          </button>
          <button className="mode-button" disabled={busy} onClick={() => void loadSettings()} type="button">
            刷新
          </button>
        </div>

        <div className="settings-status">
          {message ? <p>{message}</p> : null}
          {error ? <p className="settings-error">{error}</p> : null}
        </div>
      </div>
    </section>
  );
}

function PromptPanel() {
  const [prompts, setPrompts] = useState<PromptMeta[]>([]);
  const [activeId, setActiveId] = useState("");
  const [document, setDocument] = useState<PromptDocument | null>(null);
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function loadPromptList() {
    setBusy(true);
    setError(null);
    try {
      const result = await fetchPrompts();
      setPrompts(result.prompts);
      const nextId = activeId || result.prompts[0]?.id || "";
      if (nextId) {
        await loadPrompt(nextId);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取提示词列表失败");
    }
    setBusy(false);
  }

  async function loadPrompt(promptId: string) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const next = await fetchPrompt(promptId);
      setActiveId(next.id);
      setDocument(next);
      setContent(next.content);
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取提示词失败");
    }
    setBusy(false);
  }

  async function saveCurrentPrompt() {
    if (!document) {
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await savePrompt(document.id, content);
      setContent(result.content);
      setDocument({ ...document, content: result.content });
      setMessage("已保存，下一次模型调用会读取这份内容。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存提示词失败");
    }
    setBusy(false);
  }

  async function resetCurrentPrompt() {
    if (!document) {
      return;
    }
    if (!window.confirm(`恢复 ${document.title} 的默认内容？`)) {
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await resetPrompt(document.id);
      setContent(result.content);
      setDocument({ ...document, content: result.content });
      setMessage("已恢复默认内容。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "恢复默认失败");
    }
    setBusy(false);
  }

  useEffect(() => {
    void loadPromptList();
  }, []);

  return (
    <section className="panel prompt-panel">
      <div className="panel-title">
        <div>
          <FileText size={18} />
          <h2>提示词与处理链路</h2>
        </div>
        <button className="mode-button" disabled={busy} onClick={() => void loadPromptList()} type="button">
          刷新
        </button>
      </div>

      <div className="prompt-layout">
        <aside className="prompt-list">
          {prompts.map((item) => (
            <button
              className={activeId === item.id ? "prompt-list-item active" : "prompt-list-item"}
              key={item.id}
              onClick={() => void loadPrompt(item.id)}
              type="button"
            >
              <strong>{item.title}</strong>
              <span>{item.filename}</span>
            </button>
          ))}
        </aside>

        <div className="prompt-editor">
          {document ? (
            <>
              <div className="prompt-heading">
                <div>
                  <h3>{document.title}</h3>
                  <p>{document.description}</p>
                  <small>{document.filename}</small>
                </div>
                <div className="settings-actions">
                  <button className="primary-action" disabled={busy} onClick={() => void saveCurrentPrompt()} type="button">
                    <Save size={16} />
                    <span>保存</span>
                  </button>
                  <button className="danger-button" disabled={busy} onClick={() => void resetCurrentPrompt()} type="button">
                    恢复默认
                  </button>
                </div>
              </div>

              <textarea
                className="prompt-textarea"
                spellCheck={false}
                value={content}
                onChange={(event) => setContent(event.target.value)}
              />

              <div className="settings-status">
                {message ? <p>{message}</p> : null}
                {error ? <p className="settings-error">{error}</p> : null}
              </div>
            </>
          ) : (
            <p className="muted">{busy ? "正在加载提示词..." : "暂无提示词文件"}</p>
          )}
        </div>
      </div>
    </section>
  );
}

function displayActorName(actorId: string | null, actorNames: Record<string, string>): string {
  if (!actorId) {
    return "系统";
  }
  if (actorId === "WORLD") {
    return "世界";
  }
  if (actorId === "JUDGE") {
    return "裁定";
  }
  return actorNames[actorId] ?? actorId;
}

function fieldLabel(field: string): string {
  return {
    mood: "心情",
    energy: "精力",
    health: "健康",
    money: "金钱",
    affection: "好感",
    trust: "信任",
    respect: "尊重",
    familiarity: "熟悉度"
  }[field] ?? field;
}

function deltaOwner(item: AppliedDelta, actorNames: Record<string, string>): string {
  if (item.target === "relationship" && item.source) {
    return `${displayActorName(item.source, actorNames)} → ${displayActorName(item.ref, actorNames)}`;
  }
  return displayActorName(item.ref, actorNames);
}

function formatDelta(item: AppliedDelta): string {
  const oldValue = Number(item.old);
  const newValue = Number(item.new);
  if (Number.isFinite(oldValue) && Number.isFinite(newValue)) {
    const diff = newValue - oldValue;
    const signed = diff > 0 ? `+${diff}` : String(diff);
    return `${oldValue}→${newValue}（${signed}）`;
  }
  return `${String(item.old ?? "?")}→${String(item.new ?? "?")}`;
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
