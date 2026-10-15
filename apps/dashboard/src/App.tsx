import { useEffect, useRef, useState } from "react";
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
  Pencil,
  Plus,
  Search,
  Server,
  ShieldCheck,
  Sparkles,
  Users,
  X,
  Zap,
} from "lucide-react";

type Page = "Overview" | "Servers" | "Assistant" | "Team" | "Activity";
type Tool = {
  name: string;
  public_name: string;
  description: string;
  input_schema: Record<string, unknown>;
  member_allowed: boolean;
};
type Connection = {
  id: string;
  name: string;
  alias: string;
  description: string;
  url: string;
  auth_header?: string | null;
  auth_prefix?: string;
  has_credential?: boolean;
  enabled: boolean;
  status: "connected" | "unreachable" | "disabled";
  last_check_at?: string;
  last_success_at?: string;
  last_error?: string | null;
  tools: Tool[];
  color: string;
  symbol: string;
};
type Log = {
  id: string;
  user: string;
  action: string;
  server: string;
  time: string;
  duration_ms: number | null;
  outcome: string;
  error_category?: string | null;
};
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
  Assistant: "The chat tool loop follows after the live gateway core.",
  Team: "Good work happens together.",
  Activity: "Every action, with a little more context.",
};

function Badge({ status }: { status: Connection["status"] }) {
  const label = status === "connected" ? "Connected" : status === "disabled" ? "Disabled" : "Unreachable";
  return (
    <span className={`badge ${status === "connected" ? "green" : status === "unreachable" ? "warning" : "neutral"}`}>
      <i />
      {label}
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

type WorkspaceUser = { id: string; email: string; name: string; role: "admin" | "member" };
type Member = {
  id: string;
  name: string;
  email: string;
  role: string;
  active: boolean;
  key_status: string;
  active_keys: number;
};
type Overview = {
  connected_servers: number;
  enabled_servers: number;
  servers: number;
  tools: number;
  calls_today: number;
  members: number;
  success_rate: number | null;
  average_duration_ms: number | null;
  store_url?: string;
};

async function apiRequest<T>(path: string, csrf: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (!(["GET", "HEAD", "OPTIONS"].includes(method)) && csrf) {
    headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(`/api${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.message || `Request failed (${response.status})`);
  }
  return data as T;
}

function connectionStyle(alias: string) {
  const styles = ["plum", "sand", "blue"];
  return { color: styles[[...alias].reduce((sum, char) => sum + char.charCodeAt(0), 0) % styles.length], symbol: alias[0]?.toUpperCase() || "M" };
}

export default function App() {
  const [page, setPage] = useState<Page>("Overview");
  const [servers, setServers] = useState<Connection[]>([]);
  const [logs, setLogs] = useState<Log[]>([]);
  const [user, setUser] = useState<WorkspaceUser | null>(null);
  const [csrf, setCsrf] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [personalKeys, setPersonalKeys] = useState<{ id: string; label: string; status: string; created_at: string }[]>([]);
  const [filter, setFilter] = useState("");
  const [notice, setNotice] = useState("");
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [loginChecked, setLoginChecked] = useState(false);
  const [inviteAcceptance, setInviteAcceptance] = useState(() => new URLSearchParams(window.location.search).get("invite") || "");
  const [issuedKey, setIssuedKey] = useState("");
  const [inviteToken, setInviteToken] = useState("");
  const [modal, setModal] = useState<"server" | "member">("server");
  const [modalOpen, setModalOpen] = useState(false);
  const [editingServer, setEditingServer] = useState<Connection | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const connected = servers.filter((s) => s.enabled && s.status === "connected");
  const visibleServers = servers.filter((s) =>
    `${s.name} ${s.description}`.toLowerCase().includes(filter.toLowerCase()),
  );
  const toolCount = servers.filter((s) => s.enabled).reduce((n, s) => n + s.tools.length, 0);

  useEffect(() => {
    const incomingToken = new URLSearchParams(window.location.search).get("invite");
    if (incomingToken) sessionStorage.setItem("urumi_pending_invite", incomingToken);
    const pendingToken = inviteAcceptance || sessionStorage.getItem("urumi_pending_invite") || "";
    if (pendingToken) sessionStorage.setItem("urumi_pending_invite", pendingToken);
    else sessionStorage.removeItem("urumi_pending_invite");
    if (incomingToken) {
      const url = new URL(window.location.href);
      url.searchParams.delete("invite");
      window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    }
  }, [inviteAcceptance]);

  async function refreshWorkspace(activeUser = user, activeCsrf = csrf) {
    if (!activeUser) return;
    const [rawServers, rawOverview, rawLogs] = await Promise.all([
      apiRequest<any[]>("/servers", activeCsrf),
      apiRequest<Overview>("/overview", activeCsrf),
      apiRequest<any[]>("/audit?limit=100", activeCsrf),
    ]);
    setServers(rawServers.map((server) => ({ ...server, ...connectionStyle(server.alias) })));
    setOverview(rawOverview);
    setLogs(rawLogs.map((row) => ({
      ...row,
      time: new Date(row.time).toLocaleString(),
      outcome: row.outcome.replaceAll("_", " "),
    })));
    const ownKeys = await apiRequest<any[]>("/keys", activeCsrf);
    setPersonalKeys(ownKeys);
    if (activeUser.role === "admin") {
      setMembers(await apiRequest<Member[]>("/members", activeCsrf));
    } else {
      setMembers([{ id: activeUser.id, name: activeUser.name, email: activeUser.email, role: activeUser.role, active: true, key_status: ownKeys.some((key) => key.status === "Active") ? "Active" : "No active key", active_keys: ownKeys.filter((key) => key.status === "Active").length }]);
    }
  }

  async function refreshLiveStatus(activeCsrf = csrf) {
    const [rawServers, rawOverview, rawLogs] = await Promise.all([
      apiRequest<any[]>("/servers", activeCsrf),
      apiRequest<Overview>("/overview", activeCsrf),
      apiRequest<any[]>("/audit?limit=100", activeCsrf),
    ]);
    setServers(rawServers.map((server) => ({ ...server, ...connectionStyle(server.alias) })));
    setOverview(rawOverview);
    setLogs(rawLogs.map((row) => ({
      ...row,
      time: new Date(row.time).toLocaleString(),
      outcome: row.outcome.replaceAll("_", " "),
    })));
  }

  useEffect(() => {
    let active = true;
    apiRequest<{ user: WorkspaceUser; csrf_token: string }>("/auth/me", "")
      .then(async (session) => {
        if (!active) return;
        setUser(session.user);
        setCsrf(session.csrf_token);
        await refreshWorkspace(session.user, session.csrf_token);
      })
      .catch(() => {
        if (active) setUser(null);
      })
      .finally(() => active && setLoginChecked(true));
    return () => { active = false; };
    // Initial session bootstrap only; subsequent refreshes are user-triggered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!user) return;
    const timer = window.setInterval(() => {
      void refreshLiveStatus(csrf).catch(() => undefined);
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [user?.id, csrf]);

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (modalOpen && !element.open) element.showModal();
    if (!modalOpen && element.open) element.close();
  }, [modalOpen, modal, editingServer]);

  function navigate(next: Page) {
    setPage(next);
    setFilter("");
    setNotice("");
  }
  function openModal(kind: "server" | "member", server: Connection | null = null) {
    setModal(kind);
    setEditingServer(server);
    setModalOpen(true);
  }
  async function toggle(id: string) {
    const target = servers.find((s) => s.id === id);
    if (!target) return;
    try {
      await apiRequest(`/servers/${id}`, csrf, { method: "PATCH", body: JSON.stringify({ enabled: !target.enabled }) });
      await refreshWorkspace();
      setNotice(`${target.name} ${target.enabled ? "disabled" : "enabled"}.`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not update the server.");
    }
  }
  async function copyEndpoint() {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}/mcp`);
      setCopied(true);
      setNotice("Gateway MCP endpoint copied.");
    } catch {
      setNotice(`Copy this endpoint: ${window.location.origin}/mcp`);
    }
  }
  async function submitModal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const name = String(data.get("name")).trim();
    if (!name) return;
    setBusy(true);
    try {
      if (modal === "server") {
        const alias = String(data.get("alias") || name).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 48);
        const body = {
          alias: /^[a-z]/.test(alias) ? alias : `mcp_${alias}`.slice(0, 48),
          display_name: name,
          description: String(data.get("description") || ""),
          url: String(data.get("url")).trim(),
          auth_header: String(data.get("auth_header") || "Authorization"),
          auth_prefix: String(data.get("auth_prefix") ?? ""),
          static_credential: String(data.get("credential") || "") || null,
        };
        if (editingServer) {
          const patch: Record<string, unknown> = {
            display_name: name,
            description: body.description,
            url: body.url,
            auth_prefix: body.auth_prefix,
          };
          if (String(data.get("auth_header") || "")) patch.auth_header = body.auth_header;
          if (body.static_credential) patch.static_credential = body.static_credential;
          await apiRequest(`/servers/${editingServer.id}`, csrf, { method: "PATCH", body: JSON.stringify(patch) });
        } else {
          await apiRequest("/servers", csrf, { method: "POST", body: JSON.stringify(body) });
        }
        await refreshWorkspace();
        setNotice(editingServer ? `${name} connection updated.` : `${name} registered. Review its discovered tools and enable it when ready.`);
      } else {
        const invitation = await apiRequest<{ invite_token: string; email: string }>("/members/invitations", csrf, {
          method: "POST",
          body: JSON.stringify({ display_name: name, email: String(data.get("email")).trim(), role: "member" }),
        });
        setInviteToken(invitation.invite_token);
        setNotice(`Invitation created for ${invitation.email}. Copy the one-time token below and share it securely.`);
      }
      form.reset();
      setEditingServer(null);
      setModalOpen(false);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Could not complete this action.");
    } finally {
      setBusy(false);
    }
  }

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setLoginError("");
    try {
      const session = await apiRequest<{ user: WorkspaceUser; csrf_token: string }>("/auth/login", "", {
        method: "POST",
        body: JSON.stringify({ email: String(data.get("email")), password: String(data.get("password")) }),
      });
      setUser(session.user);
      setCsrf(session.csrf_token);
      await refreshWorkspace(session.user, session.csrf_token);
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "Sign in failed.");
    } finally {
      setBusy(false);
      setLoginChecked(true);
    }
  }

  async function acceptInvitation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setLoginError("");
    try {
      await apiRequest("/invitations/accept", "", {
        method: "POST",
        body: JSON.stringify({ token: inviteAcceptance, email: String(data.get("email")), password: String(data.get("password")) }),
      });
      const email = String(data.get("email"));
      setInviteAcceptance("");
      sessionStorage.removeItem("urumi_pending_invite");
      window.history.replaceState({}, "", window.location.pathname);
      setLoginError(`Account created for ${email}. Sign in with your new password.`);
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "Invitation could not be accepted.");
    } finally {
      setBusy(false);
      setLoginChecked(true);
    }
  }

  async function logout() {
    try { await apiRequest("/auth/logout", csrf, { method: "POST" }); } catch { /* cookie may already have expired */ }
    setUser(null);
    setServers([]);
    setLogs([]);
    setOverview(null);
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
                <td className="muted mono">{log.duration_ms === null ? "—" : `${log.duration_ms} ms`}</td>
                <td>
                  <span
                    className={`badge ${log.outcome === "success" ? "green" : log.outcome === "in progress" ? "neutral" : "warning"}`}
                  >
                    {log.outcome === "success" ? <Check size={12} /> : null}
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
  if (!loginChecked) {
    return <div className="login-shell"><div className="login-card"><span className="brand-mark">u<span>·</span></span><h1>Connecting to your gateway</h1><p>Checking your secure workspace session…</p></div></div>;
  }
  if (!user) {
    return (
      <div className="login-shell">
        {inviteAcceptance ? <form className="login-card" onSubmit={acceptInvitation}>
          <span className="brand-mark">u<span>·</span></span>
          <div className="eyebrow">TEAM INVITATION</div>
          <h1>Set up your account.</h1>
          <p>Choose a password to join the Urumi workspace.</p>
          <label>Email<input name="email" type="email" required autoComplete="username" /></label>
          <label>Password<input name="password" type="password" required minLength={12} autoComplete="new-password" /></label>
          {loginError ? <p className="login-error" role="alert">{loginError}</p> : null}
          <button className="primary full" disabled={busy}>{busy ? "Creating account…" : "Accept invitation"}<ArrowRight size={15} /></button>
        </form> : <form className="login-card" onSubmit={login}>
          <span className="brand-mark">u<span>·</span></span>
          <div className="eyebrow">URUMI TEAM GATEWAY</div>
          <h1>Welcome back.</h1>
          <p>Sign in to manage your team’s MCP connections.</p>
          <label>Email<input name="email" type="email" required autoComplete="username" placeholder="admin@example.test" /></label>
          <label>Password<input name="password" type="password" required autoComplete="current-password" /></label>
          {loginError ? <p className="login-error" role="alert">{loginError}</p> : null}
          <button className="primary full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}<ArrowRight size={15} /></button>
          <small className="muted">Use the bootstrap admin credentials from your local secret file.</small>
        </form>}
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
          <span className="workspace-icon">{user.name[0]?.toUpperCase()}</span>
          <div>
            <strong>{user.name}’s workspace</strong>
            <small>{user.role === "admin" ? "Workspace admin" : "Team member"}</small>
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
            <Avatar name={user.name} />
            <div>
              <strong>{user.name}</strong>
              <small>{user.email}</small>
            </div>
            <button className="icon-button signout" onClick={logout} aria-label="Sign out"><ExternalLink size={15} /></button>
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
            <span className="preview-pill">Live gateway</span>
            <a
              href={overview?.store_url || "http://store.localhost:8080"}
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
                {page === "Overview" ? `Welcome back, ${user.name.split(" ")[0]}` : page}
                <span className="heading-dot">.</span>
              </h1>
              <p>{descriptions[page]}</p>
            </div>
            {user.role === "admin" && (page === "Overview" || page === "Servers") ? (
              <button className="primary" onClick={() => openModal("server")}>
                <Plus size={16} /> Connect a server
              </button>
            ) : user.role === "admin" && page === "Team" ? (
              <button className="primary" onClick={() => openModal("member")}>
                <Plus size={16} /> Invite member
              </button>
            ) : null}
          </div>
          <div className="demo-note">
            <span className="demo-dot" />
            Live workspace · Registry, access controls and activity come from PostgreSQL.
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
                    {overview?.connected_servers ?? connected.length}
                    <span className="stat-denominator"> / {overview?.servers ?? servers.length}</span>
                  </strong>
                  <div className="stat-bottom">
                    <span className={servers.some((s) => s.status === "unreachable" && s.enabled) ? "warning-text" : "green-text"}>
                      <i />
                      {overview?.connected_servers === overview?.servers && overview?.servers
                        ? "All connections healthy"
                        : `${overview?.enabled_servers ?? connected.length} enabled · ${servers.filter((s) => s.status === "unreachable" && s.enabled).length} unreachable`}
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
                  <strong>{overview?.tools ?? toolCount}</strong>
                  <div className="stat-bottom">Across your enabled servers</div>
                </div>
                <div className="stat">
                  <div className="stat-top">
                    Tool calls today
                    <span className="stat-icon mist">
                      <Activity size={18} />
                    </span>
                  </div>
                  <strong>{overview?.calls_today ?? 0}</strong>
                  <div className="stat-bottom">Audited MCP requests today</div>
                </div>
                <div className="stat">
                  <div className="stat-top">
                    Team members
                    <span className="stat-icon blush">
                      <Users size={18} />
                    </span>
                  </div>
                  <strong>{overview?.members ?? members.length}</strong>
                  <div className="stat-bottom">
                    {members.slice(0, 2).map((member) => <span key={member.id} className="mini-avatar">{member.name.split(" ").map((part) => part[0]).slice(0, 2).join("")}</span>)}
                    <span>Individual keys, one endpoint</span>
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
                      <Badge status={server.status} />
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
                    Ask your workspace after the chat agent loop is connected. The MCP endpoint and policy layer are live.
                  </p>
                  <button className="prompt-example" onClick={() => navigate("Assistant")}>
                    <MessageSquare size={16} />
                    View the assistant milestone
                    <ArrowUpRight size={16} />
                  </button>
                    <button className="text-button" onClick={() => navigate("Assistant")}>
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
                <code>{window.location.host}/mcp</code>
                <button className="secondary" onClick={copyEndpoint}>
                  {copied ? <Check size={15} /> : <Clipboard size={15} />}
                  {copied ? "Copied" : "Copy MCP URL"}
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
                      <Badge status={s.status} />
                    </div>
                    <h2>{s.name}</h2>
                    <p>{s.description}</p>
                    <code className="server-url">{s.url}</code>
                    {s.last_check_at ? <p className="server-check">Checked {new Date(s.last_check_at).toLocaleTimeString()}</p> : null}
                    {s.last_error ? <p className="server-error">Last check: {s.last_error.replaceAll("_", " ")}</p> : null}
                    <details>
                      <summary>
                        {s.tools.length} tools <ChevronDown size={14} />
                      </summary>
                      <div className="tool-list">
                        {s.tools.length ? (
                          s.tools.map((tool) => (
                            <div className="tool-permission" key={tool.public_name}>
                              <code title={tool.description}>{tool.public_name}</code>
                              {user.role === "admin" ? (
                                <label className="permission-toggle">
                                  <input
                                    type="checkbox"
                                    checked={tool.member_allowed}
                                    aria-label={`Allow members to call ${tool.public_name}`}
                                    onChange={async () => {
                                      const next = s.tools.filter((item) => item.member_allowed !== (item.public_name === tool.public_name)).map((item) => item.name);
                                      try {
                                        await apiRequest(`/servers/${s.id}`, csrf, { method: "PATCH", body: JSON.stringify({ member_tools: next }) });
                                        await refreshWorkspace();
                                      } catch (error) {
                                        setNotice(error instanceof Error ? error.message : "Could not update permissions.");
                                      }
                                    }}
                                  />
                                  <span>Member</span>
                                </label>
                              ) : null}
                            </div>
                          ))
                        ) : (
                          <span>
                            Tools appear after a successful upstream discovery.
                          </span>
                        )}
                      </div>
                    </details>
                    <div className="server-actions">
                      {user.role === "admin" ? (
                        <>
                          <button className="secondary" onClick={() => toggle(s.id)}>
                            {s.enabled ? "Disable" : "Enable"}
                          </button>
                          <button className="secondary" onClick={() => openModal("server", s)}>
                            <Pencil size={14} /> Edit
                          </button>
                          <button className="secondary" onClick={async () => {
                            try {
                              const refreshed = await apiRequest<any>(`/servers/${s.id}/refresh`, csrf, { method: "POST" });
                              await refreshWorkspace();
                              setNotice(refreshed.refresh_ok ? `${s.name} catalog refreshed.` : `${s.name} is unreachable: ${refreshed.refresh_error}`);
                            } catch (error) { setNotice(error instanceof Error ? error.message : "Refresh failed."); }
                          }}>Refresh</button>
                          <button className="text-button danger" onClick={async () => {
                            if (!window.confirm(`Remove ${s.name} from the gateway?`)) return;
                            try {
                              await apiRequest(`/servers/${s.id}`, csrf, { method: "DELETE" });
                              await refreshWorkspace();
                              setNotice(`${s.name} removed from the gateway.`);
                            } catch (error) { setNotice(error instanceof Error ? error.message : "Remove failed."); }
                          }}>Remove</button>
                        </>
                      ) : <span className="muted">Managed by your admin</span>}
                    </div>
                  </section>
                ))}
              </div>
              {visibleServers.length === 0 ? (
                <p className="empty-small">No matching connections.</p>
              ) : null}
            </>
          ) : null}
          {page === "Assistant" ? (
            <section className="panel milestone-panel">
              <span className="assistant-bloom"><Sparkles size={30} /></span>
              <div>
                <div className="eyebrow">NEXT MILESTONE</div>
                <h2>Chat agent through the same gateway</h2>
                <p>The MCP endpoint is live. The next phase will add the model tool loop here, using your signed-in identity so its calls follow the same permissions and audit path.</p>
                <div className="milestone-details">
                  <span><Check size={14} /> Shared catalog and routing</span>
                  <span><Check size={14} /> Per-user keys and revocation</span>
                  <span><Activity size={14} /> Audited upstream calls</span>
                </div>
              </div>
            </section>
          ) : null}
          {page === "Team" ? (
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <h2>Your team</h2>
                  <p>Shared tools. Individual, revocable gateway keys.</p>
                </div>
                <span className="badge neutral">{members.length} members</span>
              </div>
              <section className="key-panel">
                <div>
                  <strong>Your MCP API key</strong>
                  <p>Use this key with the gateway URL. It is shown only once when created.</p>
                </div>
                <button className="secondary" disabled={busy} onClick={async () => {
                  setBusy(true);
                  try {
                    const result = await apiRequest<{ key: string }>("/keys", csrf, { method: "POST", body: JSON.stringify({ label: "Personal MCP client" }) });
                    setIssuedKey(result.key);
                    await refreshWorkspace();
                    setNotice("Your new API key is ready. Copy it now; it cannot be displayed again.");
                  } catch (error) { setNotice(error instanceof Error ? error.message : "Could not create a key."); }
                  finally { setBusy(false); }
                }}><Plus size={15} /> Generate key</button>
              </section>
              {issuedKey ? <div className="secret-reveal"><span><strong>Copy your key now</strong><code>{issuedKey}</code></span><button className="secondary" onClick={async () => { await navigator.clipboard.writeText(issuedKey); setIssuedKey(""); setNotice("API key copied. The secret has been cleared from this page."); }}>Copy key</button><button className="icon-button" aria-label="Hide key" onClick={() => setIssuedKey("")}><X size={16} /></button></div> : null}
              {inviteToken ? <div className="secret-reveal"><span><strong>One-time invite link</strong><code>{`${window.location.origin}/?invite=${encodeURIComponent(inviteToken)}`}</code></span><button className="secondary" onClick={async () => { await navigator.clipboard.writeText(`${window.location.origin}/?invite=${encodeURIComponent(inviteToken)}`); setInviteToken(""); setNotice("Invitation link copied and cleared from this page."); }}>Copy link</button><button className="icon-button" aria-label="Hide invite link" onClick={() => setInviteToken("")}><X size={16} /></button></div> : null}
              <div className="key-list">
                {personalKeys.map((key) => <div className="key-row" key={key.id}><span><strong>{key.label}</strong><small>Created {new Date(key.created_at).toLocaleDateString()}</small></span><span className={`badge ${key.status === "Active" ? "green" : "neutral"}`}>{key.status}</span>{key.status === "Active" ? <button className="text-button danger" onClick={async () => { try { await apiRequest(`/keys/${key.id}`, csrf, { method: "DELETE" }); setIssuedKey(""); await refreshWorkspace(); setNotice("API key revoked. Calls with it are rejected immediately."); } catch (error) { setNotice(error instanceof Error ? error.message : "Could not revoke key."); } }}>Revoke</button> : null}</div>)}
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Member</th>
                      <th>Role</th>
                      <th>API key status</th>
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
                            className={`badge ${member.key_status === "Active" ? "green" : "neutral"}`}
                          >
                            {member.key_status}
                          </span>
                        </td>
                        <td>
                          {member.active_keys > 0 ? (
                            <button
                              className="text-button danger"
                              onClick={async () => {
                                if (!window.confirm(`Revoke all active API keys for ${member.name}?`)) return;
                                try {
                                  const keys = await apiRequest<any[]>(`/keys?user_id=${member.id}`, csrf);
                                  await Promise.all(keys.filter((key) => key.status === "Active").map((key) => apiRequest(`/keys/${key.id}`, csrf, { method: "DELETE" })));
                                  await refreshWorkspace();
                                  setNotice(`Active API keys revoked for ${member.name}.`);
                                } catch (error) { setNotice(error instanceof Error ? error.message : "Could not revoke keys."); }
                              }}
                            >
                              Revoke keys
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
                  <p>Tool calls, rejections and outcomes attributed to their caller.</p>
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
              Urumi gateway <span className="footer-dot">·</span> Live gateway
            </span>
          </footer>
        </div>
      </main>
      <dialog ref={dialog} aria-labelledby="dialog-title" onClose={() => setModalOpen(false)}>
        <form key={`${modal}-${editingServer?.id || "new"}`} onSubmit={submitModal}>
          <div className="modal-heading">
            <h2 id="dialog-title">
              {modal === "server" ? (editingServer ? "Edit connection" : "Add a connection") : "Invite a teammate"}
            </h2>
            <button
              type="button"
              className="icon-button"
              aria-label="Close dialog"
              onClick={() => setModalOpen(false)}
            >
              <X size={19} />
            </button>
          </div>
          <p className="muted">
            {modal === "server"
              ? "Register an MCP Streamable HTTP endpoint. The credential is encrypted on the server and never returned to the browser."
              : "Create a seven-day invitation token. Share it with the teammate through your usual secure channel."}
          </p>
          <label>
            Name
            <input
              name="name"
              required
              maxLength={60}
              autoFocus
              defaultValue={modal === "server" ? editingServer?.name : ""}
              placeholder={
                modal === "server" ? "e.g. Support desk" : "Full name"
              }
            />
          </label>
          {modal === "server" ? (
            <>
              {editingServer ? <p className="muted">Alias <code>{editingServer.alias}</code> stays fixed so tool names and audit history remain stable.</p> : <label>Server alias<input name="alias" maxLength={48} placeholder="e.g. support-desk" /></label>}
              <label>Description<input name="description" maxLength={240} placeholder="What this MCP server provides" defaultValue={editingServer?.description || ""} /></label>
              <label>Server URL<input name="url" type="url" required placeholder="https://example.com/mcp" defaultValue={editingServer?.url || ""} /></label>
              <div className="form-row">
                <label>Credential header<input name="auth_header" defaultValue={editingServer ? (editingServer.auth_header ?? "") : "Authorization"} /></label>
                <label>Value prefix<input name="auth_prefix" defaultValue={editingServer?.auth_prefix ?? "Bearer "} /></label>
              </div>
              <label>Static credential<input name="credential" type="password" autoComplete="new-password" placeholder={editingServer?.has_credential ? "Leave blank to keep the current credential" : "Optional upstream token"} /></label>
            </>
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
              onClick={() => setModalOpen(false)}
            >
              Cancel
            </button>
            <button className="primary">
              {modal === "server"
                ? (editingServer ? "Save changes" : "Register server")
                : "Create invitation"}
              <ArrowRight size={15} />
            </button>
          </div>
        </form>
      </dialog>
    </div>
  );
}
