import React, { useEffect, useState } from "react";
import { Activity, Database, Play, RefreshCw, Send, Server, ShieldCheck, Zap } from "lucide-react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Summary = {
  total_requests: number;
  local_hit_rate: number;
  cloud_token_savings: number;
  p50_latency_ms: number;
  s3_local_rate: number;
};

type Distribution = { device: number; edge: number; cloud: number };

type Trace = {
  trace_id: string;
  user_input: string;
  complexity_level: string;
  privacy_level: string;
  tier: string;
  model_used: string;
  route_reason: string;
  latency_ms: number;
  cost_usd: number;
  classifier_used: string;
  privacy_desensitized: boolean;
};

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  traceId?: string | null;
  tier?: string | null;
  classifier?: string | null;
};

type DemoCase = {
  id: number;
  name: string;
  description: string;
  expected_tier: string;
};

const TRACE_LIMIT = 200;

const api = async <T,>(url: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(await response.text());
  return response.json();
};

function percent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function TierBadge({ tier }: { tier: string }) {
  return <span className={`tier tier-${tier}`}>{tier}</span>;
}

function KPICards({ summary }: { summary: Summary }) {
  const cards = [
    { label: "端/边命中率", value: percent(summary.local_hit_rate), icon: Zap, good: summary.local_hit_rate >= 0.68 },
    { label: "云端 Token 节省", value: percent(summary.cloud_token_savings), icon: Database, good: summary.cloud_token_savings >= 0.7 },
    { label: "P50 延迟", value: `${Math.round(summary.p50_latency_ms)}ms`, icon: Activity, good: summary.p50_latency_ms <= 5000 },
    { label: "S3 数据本地率", value: percent(summary.s3_local_rate), icon: ShieldCheck, good: true }
  ];
  return (
    <section className="kpi-grid" aria-label="KPI summary">
      {cards.map((card) => {
        const Icon = card.icon;
        return (
          <article className="kpi-card" key={card.label}>
            <div>
              <p>{card.label}</p>
              <strong>{card.value}</strong>
            </div>
            <Icon aria-hidden="true" className={card.good ? "icon-good" : "icon-bad"} />
          </article>
        );
      })}
    </section>
  );
}

function TierDistribution({ distribution }: { distribution: Distribution }) {
  const total = Math.max(distribution.device + distribution.edge + distribution.cloud, 1);
  const items = [
    ["device", distribution.device],
    ["edge", distribution.edge],
    ["cloud", distribution.cloud]
  ] as const;
  return (
    <section className="panel">
      <h2>实时路由分布</h2>
      <div className="stacked-bar" aria-label="routing distribution">
        {items.map(([tier, count]) => (
          <span key={tier} className={`bar bar-${tier}`} style={{ width: `${(count / total) * 100}%` }} />
        ))}
      </div>
      <div className="dist-row">
        {items.map(([tier, count]) => (
          <span key={tier}><TierBadge tier={tier} /> {count}</span>
        ))}
      </div>
    </section>
  );
}

function RoutingTrace({ traces }: { traces: Trace[] }) {
  return (
    <section className="panel trace-panel">
      <div className="trace-header">
        <h2>路由轨迹</h2>
        <span className="mono">{traces.length}/{TRACE_LIMIT}</span>
      </div>
      {traces.length === 0 ? <p className="empty">暂无轨迹，运行一个 Demo Case。</p> : null}
      <ul className="trace-list">
        {traces.map((trace) => (
          <li key={trace.trace_id}>
            <details>
              <summary>
                <TierBadge tier={trace.tier} />
                <span className="trace-input">{trace.user_input}</span>
                <span className="mono">{Math.round(trace.latency_ms)}ms</span>
                <span className="mono">${trace.cost_usd.toFixed(4)}</span>
              </summary>
              <dl>
                <dt>模型</dt><dd>{trace.model_used}</dd>
                <dt>复杂度</dt><dd>{trace.complexity_level}</dd>
                <dt>隐私</dt><dd>{trace.privacy_level}</dd>
                <dt>分类器</dt><dd>{trace.classifier_used}</dd>
                <dt>理由</dt><dd>{trace.route_reason}</dd>
              </dl>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}

function DemoRunner({ cases, onRun }: { cases: DemoCase[]; onRun: (id: number) => Promise<void> }) {
  const [running, setRunning] = useState<number | null>(null);
  return (
    <section className="panel demo-panel">
      <h2>Demo Runner</h2>
      <div className="demo-grid">
        {cases.map((demo) => (
          <button
            type="button"
            key={demo.id}
            onClick={async () => {
              setRunning(demo.id);
              await onRun(demo.id);
              setRunning(null);
            }}
            aria-busy={running === demo.id}
          >
            <Play aria-hidden="true" />
            <span>{demo.id}. {demo.name}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function ChatTester({ onComplete }: { onComplete: () => Promise<void> }) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: "assistant", content: "输入任意测试数据，我会通过混合推理网关返回结果。" }
  ]);
  const [input, setInput] = useState("");
  const [model, setModel] = useState("hybrid-auto");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const send = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const content = input.trim();
    if (!content || submitting) return;

    const nextMessages: ChatMessage[] = [...messages, { role: "user", content }];
    setMessages(nextMessages);
    setInput("");
    setSubmitting(true);
    setError("");

    try {
      const result = await api<{
        trace_id: string | null;
        tier: string | null;
        classifier: string | null;
        response: { choices: Array<{ message: { content: string } }> };
      }>("/api/demo/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model,
          messages: nextMessages.map(({ role, content }) => ({ role, content }))
        })
      });
      setMessages([
        ...nextMessages,
        {
          role: "assistant",
          content: result.response.choices[0]?.message.content ?? "",
          traceId: result.trace_id,
          tier: result.tier,
          classifier: result.classifier
        }
      ]);
      await onComplete();
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="panel chat-panel" aria-labelledby="chat-title">
      <div className="panel-header">
        <div>
          <h2 id="chat-title">聊天测试台</h2>
          <p>OpenAI 兼容入口，走完整调度链路。</p>
        </div>
        <label className="model-select">
          <span>模型</span>
          <select value={model} onChange={(event) => setModel(event.target.value)}>
            <option value="hybrid-auto">hybrid-auto</option>
            <option value="qwen3:1.5b">qwen3:1.5b</option>
            <option value="deepseek-ai/DeepSeek-V4-Flash">DeepSeek-V4-Flash</option>
          </select>
        </label>
      </div>
      <div className="chat-log" aria-live="polite">
        {messages.map((message, index) => (
          <article className={`chat-message chat-${message.role}`} key={`${message.role}-${index}`}>
            <p>{message.content}</p>
            {message.role === "assistant" && message.tier ? (
              <footer>
                <TierBadge tier={message.tier} />
                <span>{message.classifier}</span>
                <span className="mono">{message.traceId}</span>
              </footer>
            ) : null}
          </article>
        ))}
      </div>
      <form className="chat-form" onSubmit={send}>
        <label htmlFor="chat-input">测试输入</label>
        <textarea
          id="chat-input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          rows={4}
          placeholder="输入你要测试的 prompt、隐私文本或长上下文片段"
          aria-describedby={error ? "chat-error" : undefined}
          aria-invalid={Boolean(error)}
        />
        {error ? <p className="field-error" id="chat-error" role="alert">{error}</p> : null}
        <button type="submit" disabled={!input.trim() || submitting} aria-busy={submitting}>
          <Send aria-hidden="true" />
          <span>{submitting ? "发送中" : "发送"}</span>
        </button>
      </form>
    </section>
  );
}

function HealthStrip() {
  const layers = [
    ["端 Ollama", "online", "350ms"],
    ["边 vLLM", "online", "1400ms"],
    ["云 DeepSeek", "online", "4200ms"]
  ];
  return (
    <section className="health-strip" aria-label="layer health">
      {layers.map(([name, status, latency]) => (
        <span key={name}><Server aria-hidden="true" /> {name} <b>{status}</b> <em>{latency}</em></span>
      ))}
    </section>
  );
}

function Dashboard() {
  const [summary, setSummary] = useState<Summary>({ total_requests: 0, local_hit_rate: 0, cloud_token_savings: 0, p50_latency_ms: 0, s3_local_rate: 1 });
  const [distribution, setDistribution] = useState<Distribution>({ device: 0, edge: 0, cloud: 0 });
  const [traces, setTraces] = useState<Trace[]>([]);
  const [cases, setCases] = useState<DemoCase[]>([]);
  const [error, setError] = useState<string>("");

  const refresh = async () => {
    try {
      setSummary(await api<Summary>("/api/metrics/summary"));
      setDistribution(await api<Distribution>("/api/metrics/distribution"));
      setTraces(await api<Trace[]>(`/api/traces?limit=${TRACE_LIMIT}`));
      setCases(await api<DemoCase[]>("/api/demo/cases"));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "请求失败");
    }
  };

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, []);

  const runDemo = async (id: number) => {
    await api("/api/demo/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id })
    });
    await refresh();
  };

  return (
    <main>
      <header className="topbar">
        <div>
          <h1>端边云混合推理调度系统</h1>
          <p>Hybrid Device-Edge-Cloud Inference Routing Dashboard</p>
        </div>
        <button type="button" className="icon-button" aria-label="刷新数据" onClick={refresh}>
          <RefreshCw aria-hidden="true" />
        </button>
      </header>
      {error ? <p className="error" role="alert">{error}</p> : null}
      <HealthStrip />
      <KPICards summary={summary} />
      <div className="main-grid">
        <div>
          <ChatTester onComplete={refresh} />
          <TierDistribution distribution={distribution} />
          <DemoRunner cases={cases} onRun={runDemo} />
        </div>
        <RoutingTrace traces={traces} />
      </div>
    </main>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Dashboard />
  </React.StrictMode>
);
