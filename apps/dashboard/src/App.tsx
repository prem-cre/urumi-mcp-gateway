import { useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  Activity,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  ChevronRight,
  Clipboard,
  Code2,
  ExternalLink,
  LayoutDashboard,
  MessageSquare,
  Plus,
  Search,
  Send,
  Server,
  ShieldCheck,
  Sparkles,
  Users,
  X,
  Zap,
} from "lucide-react";

type Page = "Overview" | "Servers" | "Assistant" | "Team" | "Activity";
type Connection = {
  id: number;
  name: string;
  alias: string;
  description: string;
  url: string;
  enabled: boolean;
  tools: string[];
  color: string;
  symbol: string;
};
type Log = {
  id: number;
  user: string;
  action: string;
  server: string;
  time: string;
  duration: string;
  outcome: string;
};
const initialServers: Connection[] = [
  {
    id: 1,
    name: "WooCommerce",
    alias: "woocommerce",
    description: "Your store, connected.",
    url: "http://woocommerce-mcp:8000/mcp",
    enabled: true,
    tools: [
      "list_products",
      "create_product",
      "list_orders",
      "get_order",
      "update_order_status",
    ],
    color: "plum",
    symbol: "W",
  },
  {
    id: 2,
    name: "Inventory",
    alias: "inventory",
    description: "Stock levels at a glance.",
    url: "http://inventory-mcp:8000/mcp",
    enabled: true,
    tools: ["check_stock", "list_inventory", "get_low_stock"],
    color: "sand",
    symbol: "I",
  },
  {
    id: 3,
    name: "Fulfillment",
    alias: "fulfillment",
    description: "From checkout to doorstep.",
    url: "http://fulfillment-mcp:8000/mcp",
    enabled: true,
    tools: [
      "get_shipment",
      "list_shipments",
      "get_delivery_estimate",
      "list_carriers",
    ],
    color: "blue",
    symbol: "F",
  },
];
const initialLogs: Log[] = [
  {
    id: 1,
    user: "Prem Kumar",
    action: "list_orders",
    server: "WooCommerce",
    time: "2 minutes ago",
    duration: "128 ms",
    outcome: "Success",
  },
  {
    id: 2,
    user: "Alex Morgan",
    action: "check_stock",
    server: "Inventory",
    time: "8 minutes ago",
    duration: "86 ms",
    outcome: "Success",
  },
  {
    id: 3,
    user: "Prem Kumar",
    action: "create_product",
    server: "WooCommerce",
    time: "16 minutes ago",
    duration: "312 ms",
    outcome: "Success",
  },
  {
    id: 4,
    user: "Alex Morgan",
    action: "get_shipment",
    server: "Fulfillment",
    time: "24 minutes ago",
    duration: "94 ms",
    outcome: "Success",
  },
];
const nav: { name: Page; icon: typeof Server }[] = [
  { name: "Overview", icon: LayoutDashboard },
  { name: "Servers", icon: Server },
  { name: "Assistant", icon: MessageSquare },
  { name: "Team", icon: Users },
  { name: "Activity", icon: Activity },
];
const descriptions: Record<Page, string> = {
  Overview: "A little clarity for everything connected.",
  Servers: "One place for every connection your team needs.",
  Assistant: "Ask a question. See the tools behind the answer.",
  Team: "Good work happens together.",
  Activity: "Every action, with a little more context.",
};

function Badge({ enabled }: { enabled: boolean }) {
  return (
    <span className={`badge ${enabled ? "green" : "neutral"}`}>
      <i />
      {enabled ? "Connected" : "Disabled"}
    </span>
  );
}
function Avatar({ name }: { name: string }) {
  return (
    <span className={`avatar ${name.startsWith("Alex") ? "lavender" : ""}`}>
      {name
        .split(" ")
        .map((n) => n[0])
        .slice(0, 2)
        .join("")}
    </span>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("Overview");
  const [servers, setServers] = useState(initialServers);
  const [logs, setLogs] = useState(initialLogs);
  const [filter, setFilter] = useState("");
  const [notice, setNotice] = useState("");
  const [copied, setCopied] = useState(false);
  const [members, setMembers] = useState([
    {
      name: "Prem Kumar",
      email: "prem@example.test",
      role: "Admin",
      key: "Active",
    },
    {
      name: "Alex Morgan",
      email: "alex@example.test",
      role: "Member",
      key: "Active",
    },
  ]);
  const [messages, setMessages] = useState<
    { role: string; text: string; tool?: string }[]
  >([]);
  const [question, setQuestion] = useState("");
  const [modal, setModal] = useState<"server" | "member">("server");
  const dialog = useRef<HTMLDialogElement>(null);
  const connected = servers.filter((s) => s.enabled);
  const visibleServers = servers.filter((s) =>
    `${s.name} ${s.description}`.toLowerCase().includes(filter.toLowerCase()),
  );
  const toolCount = connected.reduce((n, s) => n + s.tools.length, 0);
  function navigate(next: Page) {
    setPage(next);
    setFilter("");
    setNotice("");
  }
  function openModal(kind: "server" | "member") {
    setModal(kind);
    dialog.current?.showModal();
  }
  function toggle(id: number) {
    const target = servers.find((s) => s.id === id)!;
    setServers((current) =>
      current.map((s) => (s.id === id ? { ...s, enabled: !s.enabled } : s)),
    );
    setNotice(
      `${target.name} ${target.enabled ? "disabled" : "enabled"} in this preview.`,
    );
    setLogs((current) => [
      {
        id: Date.now(),
        user: "Prem Kumar",
        action: target.enabled ? "disable_server" : "enable_server",
        server: target.name,
        time: "Just now",
        duration: "—",
        outcome: "Demo",
      },
      ...current,
    ]);
  }
  async function copyEndpoint() {
    try {
      await navigator.clipboard.writeText("https://gateway.example.com/mcp");
      setCopied(true);
      setNotice(
        "Example endpoint copied. A live gateway is not connected yet.",
      );
    } catch {
      setNotice(
        "Clipboard unavailable. Example endpoint: https://gateway.example.com/mcp",
      );
    }
  }
  function submitModal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const name = String(data.get("name")).trim();
    if (!name) return;
    if (modal === "server") {
      const url = String(data.get("url")).trim();
      const alias = name.toLowerCase().replace(/[^a-z0-9]+/g, "_");
      setServers((current) => [
        ...current,
        {
          id: Date.now(),
          name,
          alias,
          description: "New demo connection.",
          url,
          enabled: false,
          tools: [],
          color: "blue",
          symbol: name[0].toUpperCase(),
        },
      ]);
      setNotice(
        `${name} added to the preview. No connection has been attempted.`,
      );
    } else {
      setMembers((current) => [
        ...current,
        {
          name,
          email: String(data.get("email")).trim(),
          role: "Member",
          key: "Invited",
        },
      ]);
      setNotice("Demo invitation added. No email has been sent.");
    }
    event.currentTarget.reset();
    dialog.current?.close();
  }
  function ask(text: string) {
    if (!text.trim()) return;
    const woo = servers.find((s) => s.alias === "woocommerce" && s.enabled);
    const isOrdersQuestion = /orders?|today/i.test(text);
    const tool =
      woo && isOrdersQuestion ? "woocommerce__list_orders" : undefined;
    const answer = !isOrdersQuestion
      ? "This is a scripted frontend preview. Try “What orders came in today?” to see a sample response and tool trace."
      : woo
        ? "Preview response: 1 sample order today — #12, Urumi Demo Notebook, ₹299.00, processing. When the backend is connected, answers will come from your store through the gateway."
        : "WooCommerce is disabled in this preview. Enable it in Servers to restore its tools.";
    setMessages((current) => [
      ...current,
      { role: "user", text },
      { role: "assistant", text: answer, tool },
    ]);
    setQuestion("");
  }
  const activityRows = logs.filter((l) =>
    `${l.action} ${l.user} ${l.server}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  function activityTable(limit?: number) {
    return (
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Tool / action</th>
              <th>Member</th>
              <th>When</th>
              <th>Duration</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
            {activityRows.slice(0, limit).map((log) => (
              <tr key={log.id}>
                <td>
                  <span className="tool-name">{log.action}</span>
                  <small>{log.server}</small>
                </td>
                <td>
                  <span className="member-cell">
                    <Avatar name={log.user} />
                    {log.user}
                  </span>
                </td>
                <td className="muted">{log.time}</td>
                <td className="muted mono">{log.duration}</td>
                <td>
                  <span
                    className={`badge ${log.outcome === "Success" ? "green" : "neutral"}`}
                  >
                    {log.outcome === "Success" ? <Check size={12} /> : null}
                    {log.outcome}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {activityRows.length === 0 ? (
          <p className="empty-small">No activity matches your search.</p>
        ) : null}
      </div>
    );
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          href="#"
          className="brand"
          onClick={(e) => {
            e.preventDefault();
            navigate("Overview");
          }}
          aria-label="Urumi overview"
        >
          <span className="brand-mark">
            u<span>·</span>
          </span>
          urumi<span className="brand-dot">.</span>
        </a>
        <div className="workspace">
          <span className="workspace-icon">P</span>
          <div>
            <strong>Prem’s workspace</strong>
            <small>Team gateway</small>
          </div>
          <ChevronDown size={14} />
        </div>
        <span className="nav-label">WORKSPACE</span>
        <nav aria-label="Main navigation">
          {nav.map(({ name, icon: Icon }) => (
            <button
              key={name}
              className={`nav-item ${page === name ? "active" : ""}`}
              onClick={() => navigate(name)}
              aria-current={page === name ? "page" : undefined}
            >
              <Icon size={18} />
              {name}
              {name === "Servers" ? (
                <span className="nav-count">{servers.length}</span>
              ) : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="quiet-note">
            <ShieldCheck size={18} />
            <strong>One gateway. Your team.</strong>
            <p>A shared way to work with your tools.</p>
          </div>
          <div className="profile">
            <Avatar name="Prem Kumar" />
            <div>
              <strong>Prem Kumar</strong>
              <small>Workspace admin</small>
            </div>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <ChevronRight size={13} />
            <span>{page}</span>
          </div>
          <div className="topbar-right">
            <span className="preview-pill">Interactive preview</span>
            <a
              href="http://store.localhost:8080"
              target="_blank"
              rel="noreferrer"
            >
              Open store <ExternalLink size={13} />
            </a>
          </div>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <div className="eyebrow">YOUR TEAM, CONNECTED</div>
              <h1>
                {page === "Overview" ? "Welcome back, Prem" : page}
                <span className="heading-dot">.</span>
              </h1>
              <p>{descriptions[page]}</p>
            </div>
            {page === "Overview" || page === "Servers" ? (
              <button className="primary" onClick={() => openModal("server")}>
                <Plus size={16} /> Connect a server
              </button>
            ) : page === "Team" ? (
              <button className="primary" onClick={() => openModal("member")}>
                <Plus size={16} /> Invite member
              </button>
            ) : null}
          </div>
          <div className="demo-note">
            <span className="demo-dot" />
            Demo workspace · Sample data and local interactions. Live gateway
            integration comes next.
          </div>
          {notice ? (
            <div className="notice" role="status">
              {notice}
              <button
                aria-label="Dismiss message"
                onClick={() => setNotice("")}
              >
                <X size={15} />
              </button>
            </div>
          ) : null}
          {page === "Overview" ? (
            <>
              <div className="stats">
                <div className="stat">
                  <div className="stat-top">
                    Connected servers
                    <span className="stat-icon sage">
                      <Server size={18} />
                    </span>
                  </div>
                  <strong>
                    {connected.length}
                    <span className="stat-denominator">
                      {" "}
                      / {servers.length}
                    </span>
                  </strong>
                  <div className="stat-bottom">
                    <span className="green-text">
                      <i />
                      {connected.length === servers.length
                        ? "All connections healthy"
                        : "Some servers disabled"}
                    </span>
                  </div>
                </div>
                <div className="stat">
                  <div className="stat-top">
                    Available tools
                    <span className="stat-icon cream">
                      <Zap size={18} />
                    </span>
                  </div>
                  <strong>{toolCount}</strong>
                  <div className="stat-bottom">Across your enabled servers</div>
                </div>
                <div className="stat">
                  <div className="stat-top">
                    Tool calls today
                    <span className="stat-icon mist">
                      <Activity size={18} />
                    </span>
                  </div>
                  <strong>
                    148
                    <svg
                      className="sparkline"
                      viewBox="0 0 120 34"
                      aria-hidden="true"
                    >
                      <path d="M0 29 L10 23 L20 26 L30 17 L40 22 L50 13 L60 18 L70 8 L80 12 L90 5 L100 10 L120 2" />
                    </svg>
                  </strong>
                  <div className="stat-bottom">
                    <span className="green-text">
                      <ArrowUpRight size={13} />
                      18%
                    </span>
                    <span>vs. yesterday · sample</span>
                  </div>
                </div>
                <div className="stat">
                  <div className="stat-top">
                    Team members
                    <span className="stat-icon blush">
                      <Users size={18} />
                    </span>
                  </div>
                  <strong>{members.length}</strong>
                  <div className="stat-bottom">
                    <span className="mini-avatar">PK</span>
                    <span className="mini-avatar lavender">AM</span>
                    <span>Your people, one endpoint</span>
                  </div>
                </div>
              </div>
              <div className="middle-grid">
                <section className="panel connections">
                  <div className="panel-heading">
                    <div>
                      <h2>Your connections</h2>
                      <p>A small network of useful things.</p>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => navigate("Servers")}
                    >
                      Manage <ArrowRight size={14} />
                    </button>
                  </div>
                  {servers.slice(0, 3).map((server) => (
                    <button
                      className="connection-row"
                      key={server.id}
                      onClick={() => navigate("Servers")}
                    >
                      <span className={`server-icon ${server.color}`}>
                        {server.symbol}
                      </span>
                      <div className="connection-name">
                        <strong>{server.name}</strong>
                        <small>
                          {server.tools.length} tools · Streamable HTTP
                        </small>
                      </div>
                      <Badge enabled={server.enabled} />
                      <ChevronRight className="row-arrow" size={15} />
                    </button>
                  ))}
                </section>
                <section className="assistant-card">
                  <div className="assistant-bloom">
                    <Sparkles size={26} />
                  </div>
                  <span className="eyebrow">A LITTLE HELP GOES A LONG WAY</span>
                  <h2>Talk to your workspace.</h2>
                  <p>
                    Ask about orders, check inventory, or find what needs your
                    attention.
                  </p>
                  <button
                    className="prompt-example"
                    onClick={() => {
                      navigate("Assistant");
                      ask("What orders came in today?");
                    }}
                  >
                    <MessageSquare size={16} />
                    “What orders came in today?”
                    <ArrowUpRight size={16} />
                  </button>
                  <button
                    className="text-button"
                    onClick={() => navigate("Assistant")}
                  >
                    Open assistant <ArrowRight size={15} />
                  </button>
                </section>
              </div>
              <section className="panel activity-panel">
                <div className="panel-heading">
                  <div>
                    <h2>Recent activity</h2>
                    <p>A clear trail of your team’s work.</p>
                  </div>
                  <button
                    className="text-button"
                    onClick={() => navigate("Activity")}
                  >
                    View all activity <ArrowRight size={14} />
                  </button>
                </div>
                {activityTable(4)}
              </section>
              <section className="endpoint-strip">
                <span className="endpoint-icon">
                  <Code2 size={20} />
                </span>
                <div>
                  <strong>One endpoint. Every tool.</strong>
                  <p>
                    Connect your preferred MCP client to your team’s gateway.
                  </p>
                </div>
                <code>gateway.example.com/mcp</code>
                <button className="secondary" onClick={copyEndpoint}>
                  {copied ? <Check size={15} /> : <Clipboard size={15} />}
                  {copied ? "Copied example" : "Copy example URL"}
                </button>
              </section>
            </>
          ) : null}
          {page === "Servers" ? (
            <>
              <div className="section-toolbar">
                <span>
                  {connected.length} connected · {toolCount} tools available
                </span>
                <label className="search">
                  <Search size={16} />
                  <input
                    aria-label="Search servers"
                    placeholder="Search connections…"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  />
                </label>
              </div>
              <div className="server-grid">
                {visibleServers.map((s) => (
                  <section className="panel server-card" key={s.id}>
                    <div className="server-card-top">
                      <span className={`server-icon ${s.color}`}>
                        {s.symbol}
                      </span>
                      <Badge enabled={s.enabled} />
                    </div>
                    <h2>{s.name}</h2>
                    <p>{s.description}</p>
                    <code className="server-url">{s.url}</code>
                    <details>
                      <summary>
                        {s.tools.length} tools <ChevronDown size={14} />
                      </summary>
                      <div className="tool-list">
                        {s.tools.length ? (
                          s.tools.map((tool) => (
                            <code key={tool}>
                              {s.alias}__{tool}
                            </code>
                          ))
                        ) : (
                          <span>
                            Tools will be discovered when the backend is
                            connected.
                          </span>
                        )}
                      </div>
                    </details>
                    <button
                      className="secondary full"
                      onClick={() => toggle(s.id)}
                    >
                      {s.enabled ? "Disable in preview" : "Enable in preview"}
                    </button>
                  </section>
                ))}
              </div>
              {visibleServers.length === 0 ? (
                <p className="empty-small">No matching connections.</p>
              ) : null}
            </>
          ) : null}
          {page === "Assistant" ? (
            <section className="panel chat-panel">
              <div className="chat-messages" aria-live="polite">
                {messages.length === 0 ? (
                  <div className="chat-empty">
                    <span className="assistant-bloom">
                      <Sparkles size={30} />
                    </span>
                    <h2>What’s on your mind?</h2>
                    <p>
                      Explore a preview of your workspace assistant.
                      <br />
                      Responses are scripted; no model or tools are called.
                    </p>
                    <button
                      className="secondary"
                      onClick={() => ask("What orders came in today?")}
                    >
                      Try the sample orders question <ArrowUpRight size={14} />
                    </button>
                  </div>
                ) : (
                  messages.map((m, i) => (
                    <div className={`chat-message ${m.role}`} key={i}>
                      <span className="message-label">
                        {m.role === "user"
                          ? "You"
                          : "Urumi assistant · preview"}
                      </span>
                      <p>{m.text}</p>
                      {m.tool ? (
                        <span className="tool-trace">
                          <Zap size={13} />
                          Example tool: {m.tool}
                        </span>
                      ) : null}
                    </div>
                  ))
                )}
              </div>
              <form
                className="chat-input"
                onSubmit={(e) => {
                  e.preventDefault();
                  ask(question);
                }}
              >
                <input
                  aria-label="Message the preview assistant"
                  placeholder="Ask about your workspace…"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                />
                <button
                  className="primary"
                  disabled={!question.trim()}
                  aria-label="Send message"
                >
                  <Send size={18} />
                </button>
              </form>
              <p className="chat-disclaimer">
                Preview only · No live store changes or tool calls
              </p>
            </section>
          ) : null}
          {page === "Team" ? (
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>Your team</h2>
                  <p>Shared connections. Individual access.</p>
                </div>
                <span className="badge neutral">{members.length} members</span>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Member</th>
                      <th>Role</th>
                      <th>Demo key status</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((member) => (
                      <tr key={member.email}>
                        <td>
                          <span className="member-cell">
                            <Avatar name={member.name} />
                            <span>
                              {member.name}
                              <small>{member.email}</small>
                            </span>
                          </span>
                        </td>
                        <td>{member.role}</td>
                        <td>
                          <span
                            className={`badge ${member.key === "Active" ? "green" : "neutral"}`}
                          >
                            {member.key}
                          </span>
                        </td>
                        <td>
                          {member.role === "Admin" ? (
                            <span className="muted">You</span>
                          ) : member.key === "Active" ? (
                            <button
                              className="text-button"
                              onClick={() => {
                                setMembers((current) =>
                                  current.map((m) =>
                                    m.email === member.email
                                      ? { ...m, key: "Revoked" }
                                      : m,
                                  ),
                                );
                                setNotice(
                                  "Demo key marked revoked. No real API key was changed.",
                                );
                              }}
                            >
                              Revoke demo key
                            </button>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}
          {page === "Activity" ? (
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>Workspace activity</h2>
                  <p>Sample tool calls and your preview actions.</p>
                </div>
                <label className="search">
                  <Search size={16} />
                  <input
                    aria-label="Search activity"
                    placeholder="Search activity…"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                  />
                </label>
              </div>
              {activityTable()}
            </section>
          ) : null}
          <footer>
            <span>Made for the way your team works.</span>
            <span>
              Urumi gateway <span className="footer-dot">·</span> Frontend
              preview
            </span>
          </footer>
        </div>
      </main>
      <dialog ref={dialog} aria-labelledby="dialog-title">
        <form onSubmit={submitModal}>
          <div className="modal-heading">
            <h2 id="dialog-title">
              {modal === "server" ? "Add a connection" : "Invite a teammate"}
            </h2>
            <button
              type="button"
              className="icon-button"
              aria-label="Close dialog"
              onClick={() => dialog.current?.close()}
            >
              <X size={19} />
            </button>
          </div>
          <p className="muted">
            {modal === "server"
              ? "Create a demo entry. Live connections come with the backend."
              : "Preview a team invitation. No email will be sent."}
          </p>
          <label>
            Name
            <input
              name="name"
              required
              maxLength={60}
              autoFocus
              placeholder={
                modal === "server" ? "e.g. Support desk" : "Full name"
              }
            />
          </label>
          {modal === "server" ? (
            <label>
              Server URL
              <input
                name="url"
                type="url"
                required
                placeholder="https://example.com/mcp"
              />
            </label>
          ) : (
            <label>
              Email
              <input
                name="email"
                type="email"
                required
                placeholder="teammate@example.test"
              />
            </label>
          )}
          <div className="modal-actions">
            <button
              type="button"
              className="secondary"
              onClick={() => dialog.current?.close()}
            >
              Cancel
            </button>
            <button className="primary">
              {modal === "server"
                ? "Add demo connection"
                : "Add demo invitation"}
              <ArrowRight size={15} />
            </button>
          </div>
        </form>
      </dialog>
    </div>
  );
}
