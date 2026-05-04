import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  Brain,
  CheckCircle2,
  Clock3,
  Database,
  GitBranch,
  RefreshCw,
  Search,
  ShieldCheck,
  TerminalSquare,
} from "lucide-react";
import "./styles.css";

type HealthPayload = { status?: string; ready_models?: string[]; reasons?: string[] };

type EvidencePayload = {
  dataset: {
    manifest: {
      row_count: number;
      train_row_count: number;
      validation_row_count: number;
      test_row_count: number;
      document_count: number;
      task_order: string[];
    };
    quality_report: {
      rows_by_split: Record<string, number>;
      rows_by_task: Record<string, number>;
      rows_by_source_scheme: Record<string, number>;
      avg_input_chars: number;
      avg_output_chars: number;
    };
  };
  evaluation: {
    baseline: ModelMetrics;
    tuned: ModelMetrics;
  };
  feishu_acceptance: {
    latest_passed: AcceptanceRun[];
    all_run_count: number;
    passed_run_count: number;
  };
};

type ModelMetrics = {
  model_id: string;
  sample_count: number;
  success_count: number;
  failure_count: number;
  avg_latency_ms: number;
  avg_format_compliance: number;
  avg_char_f1: number;
};

type AcceptanceRun = {
  scenario: string;
  report_dir: string;
  trace_token_round_1: string;
  trace_token_round_2: string;
  request_id: string;
  status_code: number;
  passed: boolean;
};

type MemoryReport = {
  enabled?: boolean;
  event_count?: number;
  active_memory_count?: number;
  superseded_memory_count?: number;
  retrieval_count?: number;
  retrieval_hit_count?: number;
  hit_rate?: number;
  avg_retrieval_latency_ms?: number;
  avg_injected_chars?: number;
  version_correctness?: number;
};

type MemorySearch = {
  query: string;
  hit_count: number;
  cards: MemoryCard[];
  metrics: {
    hit_at_1?: number;
    retrieval_latency_ms?: number;
    evaluated_active_memory_count?: number;
    fts_candidate_count?: number;
  };
};

type MemoryCard = {
  id: string;
  topic: string;
  decision: string;
  conclusion: string;
  status: string;
  version: number;
  source_url: string;
  occurred_at: string;
  score: number;
};

type LoadState = {
  health?: HealthPayload;
  ready?: HealthPayload;
  evidence?: EvidencePayload;
  memoryReport?: MemoryReport;
  interference?: MemorySearch;
  conflict?: MemorySearch;
  efficiency?: MemorySearch;
  error?: string;
  loading: boolean;
  updatedAt?: string;
};

const memoryQueries = {
  interference: "竞赛运行时不用 legacy systemd 时应该使用哪些脚本？",
  conflict: "竞赛运行时 request_timeout_ms 使用多少？",
  efficiency: "基线是什么？",
};

const commercialIdentity = {
  organization: "RuyiAI-Stack",
  site: "ruyiai-stack.github.io",
  repository: "RuyiAI-Stack/ruyiai-stack.github.io",
};

async function readJson<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

async function readOptional<T>(path: string): Promise<T | undefined> {
  try {
    return await readJson<T>(path);
  } catch {
    return undefined;
  }
}

async function loadConsoleData(): Promise<Omit<LoadState, "loading">> {
  const [health, ready, evidence, memoryReport] = await Promise.all([
    readOptional<HealthPayload>("/health"),
    readOptional<HealthPayload>("/ready"),
    readOptional<EvidencePayload>("/v1/competition/feishu-office/evidence"),
    readOptional<MemoryReport>("/v1/memory/report"),
  ]);
  const searchParams = new URLSearchParams({
    tenant_id: "tenant-real",
    project_id: "feishu-office",
    conversation_id: "oc_group_trace_room",
    limit: "3",
  });
  const [interference, conflict, efficiency] = await Promise.all([
    readOptional<MemorySearch>(
      `/v1/memory/search?${searchParams.toString()}&query=${encodeURIComponent(
        memoryQueries.interference,
      )}`,
    ),
    readOptional<MemorySearch>(
      `/v1/memory/search?${searchParams.toString()}&query=${encodeURIComponent(
        memoryQueries.conflict,
      )}`,
    ),
    readOptional<MemorySearch>(
      `/v1/memory/search?${searchParams.toString()}&query=${encodeURIComponent(
        memoryQueries.efficiency,
      )}`,
    ),
  ]);
  return {
    health,
    ready,
    evidence,
    memoryReport,
    interference,
    conflict,
    efficiency,
    updatedAt: new Date().toLocaleTimeString("zh-CN", { hour12: false }),
  };
}

function formatMs(value?: number): string {
  if (value === undefined || Number.isNaN(value)) return "--";
  if (value >= 1000) return `${(value / 1000).toFixed(1)} s`;
  return `${value.toFixed(1)} ms`;
}

function formatRatio(value?: number): string {
  if (value === undefined || Number.isNaN(value)) return "--";
  return `${Math.round(value * 100)}%`;
}

function scenarioLabel(value: string): string {
  const labels: Record<string, string> = {
    "dm-nonstream": "DM 非流式",
    "dm-stream": "DM 流式",
    "group-at-nonstream": "群聊 @bot 非流式",
    "group-at-stream": "群聊 @bot 流式",
  };
  return labels[value] || value;
}

function App() {
  const [state, setState] = useState<LoadState>({ loading: true });

  async function refresh() {
    setState((current) => ({ ...current, loading: true, error: undefined }));
    try {
      const data = await loadConsoleData();
      setState({ ...data, loading: false });
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      }));
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  const latencyLift = useMemo(() => {
    const baseline = state.evidence?.evaluation.baseline.avg_latency_ms;
    const tuned = state.evidence?.evaluation.tuned.avg_latency_ms;
    if (!baseline || !tuned) return "--";
    return `${Math.round((1 - tuned / baseline) * 100)}%`;
  }, [state.evidence]);
  const hasEvidence = Boolean(state.evidence);

  return (
    <main className="shell">
      <aside className="rail">
        <div className="brandMark">
          <Brain size={18} />
        </div>
        <nav aria-label="Demo sections">
          <a href="#overview" aria-label="Overview">
            <Activity size={18} />
          </a>
          <a href="#memory" aria-label="Memory">
            <Search size={18} />
          </a>
          <a href="#acceptance" aria-label="Acceptance">
            <ShieldCheck size={18} />
          </a>
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div className="titleBlock">
            <p className="eyebrow">飞书企业级记忆引擎</p>
            <h1>LarkMemoryCore</h1>
          </div>
          <div className="topbarActions">
            <span className={hasEvidence ? "statusDot" : "statusDot bad"}>
              {hasEvidence ? "真实数据" : "等待证据"}
            </span>
            <button className="iconButton" type="button" onClick={() => void refresh()}>
              <RefreshCw size={16} className={state.loading ? "spin" : ""} />
              刷新
            </button>
          </div>
        </header>

        <CommercialIdentity />

        {!hasEvidence ? (
          <section className="notice">
            <TerminalSquare size={18} />
            <div>
              <strong>等待竞赛运行时</strong>
              <p>{state.error || "请确认 18100 API 已启动，并完成本地代理认证。"}</p>
              <code>
                python3 -m competition.feishu_office.seed_memory_engine --base-url
                http://127.0.0.1:18100 --api-key "&lt;runtime-api-key&gt;"
              </code>
            </div>
          </section>
        ) : null}

        <section className="overview" id="overview">
          <Metric
            icon={<Database size={16} />}
            label="真实数据行"
            value={state.evidence?.dataset.manifest.row_count ?? "--"}
            detail={`${state.evidence?.dataset.manifest.document_count ?? "--"} documents`}
          />
          <Metric
            icon={<Brain size={16} />}
            label="Active 记忆"
            value={state.memoryReport?.active_memory_count ?? "--"}
            detail={`${state.memoryReport?.superseded_memory_count ?? "--"} superseded`}
          />
          <Metric
            icon={<GitBranch size={16} />}
            label="版本正确率"
            value={formatRatio(state.memoryReport?.version_correctness)}
            detail={`${state.memoryReport?.retrieval_count ?? "--"} retrievals`}
          />
          <Metric
            icon={<Clock3 size={16} />}
            label="调优延迟下降"
            value={latencyLift}
            detail={formatMs(state.evidence?.evaluation.tuned.avg_latency_ms)}
          />
        </section>

        <section className="grid two">
          <Panel title="数据集" subtitle="真实材料拆分">
            <SplitRows report={state.evidence?.dataset.quality_report} />
          </Panel>
          <Panel title="模型评测" subtitle="只显示指标">
            <ModelComparison evidence={state.evidence} />
          </Panel>
        </section>

        <section className="memoryArea" id="memory">
          <Panel title="记忆检索" subtitle={`刷新时间 ${state.updatedAt ?? "--"}`}>
            <div className="queryGrid">
              <QueryBlock title="抗干扰" result={state.interference} />
              <QueryBlock title="矛盾更新" result={state.conflict} />
              <QueryBlock title="效能补全" result={state.efficiency} />
            </div>
          </Panel>
        </section>

        <section className="grid two" id="acceptance">
          <Panel title="飞书验收" subtitle="四场景通过记录">
            <AcceptanceList runs={state.evidence?.feishu_acceptance.latest_passed ?? []} />
          </Panel>
          <Panel title="运行状态">
            <RuntimeStatus health={state.health} ready={state.ready} report={state.memoryReport} />
          </Panel>
        </section>
      </section>
    </main>
  );
}

function CommercialIdentity() {
  return (
    <section className="identityBar" aria-label="Commercial identity">
      <strong>{commercialIdentity.organization}</strong>
      <a href={`https://${commercialIdentity.site}`} target="_blank" rel="noreferrer">
        {commercialIdentity.site}
      </a>
      <a
        className="identityRepo"
        href={`https://github.com/${commercialIdentity.repository}`}
        target="_blank"
        rel="noreferrer"
      >
        {commercialIdentity.repository}
      </a>
    </section>
  );
}

function Metric({
  icon,
  label,
  value,
  detail,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  detail: string;
}) {
  return (
    <article className="metric">
      <div className="metricIcon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function Panel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="panel">
      <header>
        <div>
          <h2>{title}</h2>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
      </header>
      {children}
    </section>
  );
}

function SplitRows({ report }: { report?: EvidencePayload["dataset"]["quality_report"] }) {
  const rows = report?.rows_by_split ?? {};
  const total = Object.values(rows).reduce((sum, value) => sum + value, 0);
  return (
    <div className="splitRows">
      {Object.entries(rows).map(([name, value]) => (
        <div className="splitLine" key={name}>
          <span>{name}</span>
          <div className="bar">
            <i style={{ width: `${total ? (value / total) * 100 : 0}%` }} />
          </div>
          <strong>{value}</strong>
        </div>
      ))}
      <div className="metaLine">
        <span>repo / https</span>
        <strong>
          {report?.rows_by_source_scheme.repo ?? "--"} /{" "}
          {report?.rows_by_source_scheme.https ?? "--"}
        </strong>
      </div>
    </div>
  );
}

function ModelComparison({ evidence }: { evidence?: EvidencePayload }) {
  const baseline = evidence?.evaluation.baseline;
  const tuned = evidence?.evaluation.tuned;
  return (
    <div className="modelCompare">
      {[baseline, tuned].map((model, index) => (
        <div className="modelRow" key={model?.model_id ?? index}>
          <span>{index === 0 ? "baseline" : "tuned"}</span>
          <strong>{model ? formatMs(model.avg_latency_ms) : "--"}</strong>
          <small>
            {model?.success_count ?? "--"} / {model?.sample_count ?? "--"} success
          </small>
        </div>
      ))}
    </div>
  );
}

function QueryBlock({ title, result }: { title: string; result?: MemorySearch }) {
  const top = result?.cards[0];
  return (
    <article className="queryBlock">
      <div className="queryHeader">
        <span>{title}</span>
        <strong>{result?.hit_count ?? "--"} hits</strong>
      </div>
      {top ? (
        <>
          <h3>{top.topic}</h3>
          <p>{top.conclusion}</p>
          <div className="cardMeta">
            <span>v{top.version}</span>
            <span>{top.status}</span>
            <span>{top.score.toFixed(2)}</span>
          </div>
          <code>{top.source_url}</code>
        </>
      ) : (
        <p className="muted">暂无命中。请先运行记忆种子脚本。</p>
      )}
    </article>
  );
}

function AcceptanceList({ runs }: { runs: AcceptanceRun[] }) {
  return (
    <div className="acceptanceList">
      {runs.map((run) => (
        <div className="acceptanceItem" key={run.scenario}>
          <CheckCircle2 size={16} />
          <div>
            <strong>{scenarioLabel(run.scenario)}</strong>
            <span>{run.trace_token_round_2}</span>
          </div>
          <code>{run.status_code}</code>
        </div>
      ))}
    </div>
  );
}

function RuntimeStatus({
  health,
  ready,
  report,
}: {
  health?: HealthPayload;
  ready?: HealthPayload;
  report?: MemoryReport;
}) {
  return (
    <div className="runtimeStatus">
      <div>
        <span>health</span>
        <strong>{health?.status ?? "--"}</strong>
      </div>
      <div>
        <span>ready</span>
        <strong>{ready?.status ?? "--"}</strong>
      </div>
      <div>
        <span>memory</span>
        <strong>{report?.enabled ? "enabled" : "disabled"}</strong>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
