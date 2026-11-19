import { FormEvent, useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, CircleUserRound, LogOut, ShieldCheck, Users } from 'lucide-react';
import App from '../App';
import yashLogo from '../assets/Yash logo.jpg';
import './auth.css';

type User = { username: string; role: 'admin' | 'user'; active?: boolean };
type FeedbackItem = { feedback_id: string; username: string; rating: 'helpful' | 'unhelpful'; correction?: string | null; created_at: string; agent: string; question: string; answer: string };

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Unable to complete the request. Please try again.');
  }
  return response.status === 204 ? undefined as T : response.json();
}

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError('');
    try {
      const user = await request<User>('/auth/login', { method: 'POST', body: JSON.stringify({ username: username.trim(), password }) });
      setPassword(''); onLogin(user);
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to sign in.'); }
    finally { setBusy(false); }
  }
  return <main className="auth-page">
    <div className="auth-page-top"><a href="/" className="auth-wordmark"><img src={yashLogo} alt="YASH Technologies" /><span>AGENTIC AI</span></a><a href="/" className="auth-home"><ArrowLeft size={15} /> Home</a></div>
    <div className="auth-layout">
      <section className="auth-intro"><span className="auth-eyebrow"><span className="auth-dot" /> SAP PLANNING INTELLIGENCE</span><h1>Good decisions<br /><em>start here.</em></h1><p>Your AI specialists and planning workspace are ready when you are.</p><div className="auth-intro-rule" /><div className="auth-intro-caption"><ShieldCheck size={18} /><span>YOUR PLANNING WORKSPACE<br /><strong>One secure place to get to work.</strong></span></div></section>
      <section className="auth-card" aria-labelledby="auth-title"><div className="auth-card-heading"><span className="auth-card-icon"><CircleUserRound size={20} /></span><span>ACCOUNT ACCESS</span></div><h2 id="auth-title">Welcome back.</h2><p>Sign in to open your agent workspace.</p>
        <form onSubmit={submit} className="auth-form"><label htmlFor="auth-username">Username</label><input id="auth-username" autoComplete="username" autoFocus value={username} onChange={e => setUsername(e.target.value)} required disabled={busy} placeholder="Your username" /><label htmlFor="auth-password">Password</label><input id="auth-password" type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} required disabled={busy} placeholder="Your password" />
          {error && <div className="auth-alert" role="alert">{error}</div>}
          <button className="auth-primary" type="submit" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}<ArrowRight size={18} /></button>
        </form><p className="auth-help">Need access? Contact your workspace administrator.</p>
      </section>
    </div><footer className="auth-footer">YASH TECHNOLOGIES <span>·</span> AGENTIC AI</footer>
  </main>;
}

function UserManager({ currentUser, onBack }: { currentUser: User; onBack: () => void }) {
  const [users, setUsers] = useState<User[]>([]);
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('');
  const [role, setRole] = useState<'admin' | 'user'>('user');
  const [resetUser, setResetUser] = useState(''); const [resetPassword, setResetPassword] = useState('');
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(''); const [notice, setNotice] = useState('');
  const [view, setView] = useState<'users' | 'feedback'>('users');
  const [feedback, setFeedback] = useState<FeedbackItem[]>([]);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  async function refresh() { const data = await request<{ users: User[] }>('/auth/users'); setUsers(data.users); }
  useEffect(() => { refresh().catch(e => setError(e instanceof Error ? e.message : 'Unable to load users.')).finally(() => setLoading(false)); }, []);
  async function perform(action: () => Promise<void>, success: string) {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try { await action(); await refresh(); setNotice(success); }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to update user.'); }
    finally { setBusy(false); }
  }
  function create(event: FormEvent) {
    event.preventDefault();
    void perform(async () => { await request('/auth/users', { method: 'POST', body: JSON.stringify({ username: username.trim(), password, role }) }); setUsername(''); setPassword(''); setRole('user'); }, 'User created.');
  }
  function update(user: User, changes: object, message: string) {
    void perform(async () => { await request(`/auth/users/${encodeURIComponent(user.username)}`, { method: 'PATCH', body: JSON.stringify(changes) }); }, message);
  }
  function reset(event: FormEvent) {
    event.preventDefault();
    const user = users.find(item => item.username === resetUser);
    if (!user) return;
    void perform(async () => { await request(`/auth/users/${encodeURIComponent(user.username)}`, { method: 'PATCH', body: JSON.stringify({ password: resetPassword }) }); setResetUser(''); setResetPassword(''); }, 'Password updated.');
  }
  async function openFeedback() {
    setView('feedback'); setFeedbackLoading(true); setError(''); setNotice('');
    try { const data = await request<{ feedback: FeedbackItem[] }>('/auth/feedback'); setFeedback(data.feedback); }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to load feedback.'); }
    finally { setFeedbackLoading(false); }
  }
  return <main className="auth-admin"><div className="auth-admin-top"><button className="auth-back" onClick={onBack}><ArrowLeft size={16} /> Back to workspace</button><span><ShieldCheck size={16} /> ADMINISTRATION</span></div>
    <div className="auth-admin-content"><div className="auth-admin-title"><div><span className="auth-overline">WORKSPACE ADMINISTRATION</span><h1>{view === 'users' ? 'Workspace users.' : 'Response feedback.'}</h1><p>{view === 'users' ? 'Create accounts and manage who can access the agents.' : 'Review feedback and corrections submitted by agent users.'}</p></div>{view === 'users' && <span className="auth-admin-count"><Users size={18} /> {users.length} {users.length === 1 ? 'user' : 'users'}</span>}</div>
      <nav className="auth-admin-nav" aria-label="Administration"><button type="button" aria-current={view === 'users' ? 'page' : undefined} onClick={() => { setView('users'); setError(''); setNotice(''); }}>Users</button><button type="button" aria-current={view === 'feedback' ? 'page' : undefined} onClick={() => void openFeedback()}>Feedback review</button></nav>
      {error && <div className="auth-alert" role="alert">{error}</div>}{notice && <div className="auth-success" role="status"><Check size={16} /> {notice}</div>}
      {view === 'feedback' ? <section className="auth-admin-panel"><div className="auth-panel-heading"><div><span>REVIEW QUEUE</span><h2>Agent feedback</h2></div><button className="auth-secondary" type="button" disabled={feedbackLoading} onClick={() => void openFeedback()}>Refresh</button></div>{feedbackLoading ? <p className="auth-muted" role="status">Loading feedback…</p> : feedback.length === 0 ? <p className="auth-muted">No feedback has been submitted yet.</p> : <div className="auth-feedback-list">{feedback.map(item => <article className="auth-feedback-item" key={item.feedback_id}><div className="auth-feedback-meta"><strong>{item.rating === 'helpful' ? 'Helpful' : 'Not helpful'}</strong><span>{item.agent}</span><span>{item.username}</span><time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></div><p><b>Request:</b> {item.question}</p>{item.correction && <p className="auth-feedback-correction"><b>Suggested correction:</b> {item.correction}</p>}<details><summary>View agent response</summary><p>{item.answer}</p></details></article>)}</div>}</section> : <div className="auth-admin-grid"><section className="auth-admin-panel"><div className="auth-panel-heading"><div><span>DIRECTORY</span><h2>Users</h2></div></div>
        {loading ? <p className="auth-muted" role="status">Loading users…</p> : users.length === 0 ? <p className="auth-muted">No users found.</p> : <div className="auth-user-list">{users.map(user => <div className="auth-user" key={user.username}><div className="auth-user-icon">{user.username.slice(0, 1).toUpperCase()}</div><div className="auth-user-details"><strong>{user.username} {user.username === currentUser.username && <small>YOU</small>}</strong><span>{user.active === false ? 'Disabled' : 'Active'}</span></div><label className="auth-role-label"><span className="auth-visually-hidden">Role for {user.username}</span><select value={user.role} disabled={busy || user.username === currentUser.username} onChange={e => update(user, { role: e.target.value }, 'Role updated.')}><option value="user">Agent user</option><option value="admin">Admin</option></select></label><button className="auth-text-button" disabled={busy} onClick={() => { setResetUser(user.username); setResetPassword(''); }}>Reset password</button><button className="auth-text-button" disabled={busy || user.username === currentUser.username} onClick={() => update(user, { active: user.active === false }, user.active === false ? 'User enabled.' : 'User disabled.')}>{user.active === false ? 'Enable' : 'Disable'}</button></div>)}</div>}
        {resetUser && <form className="auth-reset" onSubmit={reset}><div><strong>Reset password</strong><p>Set a new password for {resetUser}. Use at least 12 characters.</p></div><label htmlFor="auth-reset-password">New password</label><input id="auth-reset-password" type="password" autoComplete="new-password" value={resetPassword} onChange={e => setResetPassword(e.target.value)} minLength={12} required /><div className="auth-reset-actions"><button type="button" className="auth-secondary" onClick={() => { setResetUser(''); setResetPassword(''); }}>Cancel</button><button type="submit" className="auth-primary" disabled={busy}>Save password</button></div></form>}
      </section><section className="auth-admin-panel auth-create-panel"><div className="auth-panel-heading"><div><span>NEW ACCOUNT</span><h2>Add a user</h2></div></div><p className="auth-muted">Create an account for someone on your team.</p><form className="auth-form" onSubmit={create}><label htmlFor="new-user">Username</label><input id="new-user" value={username} onChange={e => setUsername(e.target.value)} autoComplete="off" required /><label htmlFor="new-password">Password (at least 12 characters)</label><input id="new-password" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="new-password" minLength={12} required /><label htmlFor="new-role">Role</label><select id="new-role" value={role} onChange={e => setRole(e.target.value as 'admin' | 'user')}><option value="user">Agent user</option><option value="admin">Admin</option></select><p className="auth-role-note">Agent users can run agents and approve actions. Admins can also manage accounts.</p><button type="submit" className="auth-primary" disabled={busy}>{busy ? 'Saving…' : 'Create user'} <ArrowRight size={16} /></button></form></section></div>}
    </div></main>;
}

export default function AuthApp() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true); const [management, setManagement] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { request<User>('/auth/me').then(setUser).catch(() => setUser(null)).finally(() => setChecking(false)); }, []);
  async function logout() {
    try { await request('/auth/logout', { method: 'POST' }); setManagement(false); setUser(null); }
    catch { setError('Unable to sign out. Please try again.'); }
  }
  if (checking) return <main className="auth-checking" role="status">Opening your workspace…</main>;
  if (!user) return <Login onLogin={setUser} />;
  if (management && user.role === 'admin') return <UserManager currentUser={user} onBack={() => setManagement(false)} />;
  return <><div className="auth-account-bar"><span className="auth-account-name"><CircleUserRound size={15} /> {user.username} <span className="auth-account-role">{user.role === 'admin' ? 'Admin' : 'Agent user'}</span></span><div>{user.role === 'admin' && <button onClick={() => setManagement(true)}><Users size={14} /> Manage users</button>}<button onClick={() => void logout()}><LogOut size={14} /> Sign out</button></div></div>{error && <div className="auth-account-error" role="alert">{error}</div>}<App key={user.username} username={user.username} /></>;
}
