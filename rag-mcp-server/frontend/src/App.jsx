import React, { useState, useEffect } from 'react'
import Dashboard from './pages/Dashboard'
import Documents from './pages/Documents'
import Search from './pages/Search'
import SMBBrowser from './pages/SMBBrowser'
import MCPConfig from './pages/MCPConfig'
import APIKeys from './pages/APIKeys'
import {
  checkBootstrap,
  createAPIKey,
  getStatus,
  getToken,
  setToken,
  clearToken,
} from './services/api'

const PAGES = [
  { id: 'dashboard', label: 'Dashboard', icon: '~' },
  { id: 'documents', label: 'Documents', icon: '#' },
  { id: 'search', label: 'Search', icon: '?' },
  { id: 'smb', label: 'SMB Browser', icon: '>' },
  { id: 'mcp', label: 'MCP Server', icon: '*' },
  { id: 'apikeys', label: 'API Keys', icon: 'K' },
]

const PAGE_COMPONENTS = {
  dashboard: Dashboard,
  documents: Documents,
  search: Search,
  smb: SMBBrowser,
  mcp: MCPConfig,
  apikeys: APIKeys,
}

function BootstrapForm({ onDone }) {
  const [name, setName] = useState('admin')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [newKey, setNewKey] = useState('')

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      const res = await createAPIKey(name, 'Bootstrap admin key', true)
      setNewKey(res.key)
    } catch (e) {
      setErr(e.message)
    } finally {
      setBusy(false)
    }
  }

  function proceed() {
    setToken(newKey)
    onDone()
  }

  if (newKey) {
    return (
      <div className="auth-screen">
        <div className="auth-card">
          <h2>Save this admin key</h2>
          <p>This is the only time it will be shown. Store it somewhere safe.</p>
          <pre className="key-display">{newKey}</pre>
          <button onClick={proceed}>I've saved it — continue</button>
        </div>
      </div>
    )
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <h2>Welcome — create the first admin key</h2>
        <p>No keys exist yet. Create one to lock down this server.</p>
        <form onSubmit={submit}>
          <label>Key name</label>
          <input value={name} onChange={e => setName(e.target.value)} required />
          {err && <div className="auth-error">{err}</div>}
          <button disabled={busy} type="submit">{busy ? 'Creating…' : 'Create admin key'}</button>
        </form>
      </div>
    </div>
  )
}

function LoginForm({ onDone }) {
  const [key, setKey] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    setToken(key)
    try {
      await getStatus()
      onDone()
    } catch (e) {
      clearToken()
      setErr('Invalid API key')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <h2>Sign in</h2>
        <p>Paste your admin API key to manage this server.</p>
        <form onSubmit={submit}>
          <label>API key</label>
          <input
            type="password"
            value={key}
            onChange={e => setKey(e.target.value)}
            placeholder="rmcp_…"
            required
            autoFocus
          />
          {err && <div className="auth-error">{err}</div>}
          <button disabled={busy || !key} type="submit">
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}

export default function App() {
  const [activePage, setActivePage] = useState('dashboard')
  const [authState, setAuthState] = useState('checking') // checking | bootstrap | login | ready
  const PageComponent = PAGE_COMPONENTS[activePage]

  useEffect(() => { evaluateAuth() }, [])

  async function evaluateAuth() {
    try {
      const { bootstrap_required } = await checkBootstrap()
      if (bootstrap_required) {
        setAuthState('bootstrap')
        return
      }
      if (!getToken()) {
        setAuthState('login')
        return
      }
      try {
        await getStatus()
        setAuthState('ready')
      } catch {
        clearToken()
        setAuthState('login')
      }
    } catch {
      setAuthState('login')
    }
  }

  function signOut() {
    clearToken()
    setAuthState('login')
  }

  if (authState === 'checking') return <div className="auth-screen"><p>Loading…</p></div>
  if (authState === 'bootstrap') return <BootstrapForm onDone={evaluateAuth} />
  if (authState === 'login') return <LoginForm onDone={() => setAuthState('ready')} />

  return (
    <div className="app-layout">
      <nav className="sidebar">
        <div className="sidebar-header">
          <h1>RAG MCP Server</h1>
          <p>BrownserverN5 &middot; 192.168.1.52</p>
        </div>
        {PAGES.map(page => (
          <button
            key={page.id}
            className={`nav-item ${activePage === page.id ? 'active' : ''}`}
            onClick={() => setActivePage(page.id)}
          >
            <span style={{ fontFamily: 'monospace', width: '1.2em', textAlign: 'center' }}>{page.icon}</span>
            {page.label}
          </button>
        ))}
        <button className="nav-item" onClick={signOut} style={{ marginTop: 'auto' }}>
          <span style={{ fontFamily: 'monospace', width: '1.2em', textAlign: 'center' }}>X</span>
          Sign out
        </button>
      </nav>
      <main className="main-content">
        <PageComponent />
      </main>
    </div>
  )
}
