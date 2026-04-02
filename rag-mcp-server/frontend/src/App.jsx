import React, { useState } from 'react'
import Dashboard from './pages/Dashboard'
import Documents from './pages/Documents'
import Search from './pages/Search'
import SMBBrowser from './pages/SMBBrowser'
import MCPConfig from './pages/MCPConfig'
import APIKeys from './pages/APIKeys'

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

export default function App() {
  const [activePage, setActivePage] = useState('dashboard')
  const PageComponent = PAGE_COMPONENTS[activePage]

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
      </nav>
      <main className="main-content">
        <PageComponent />
      </main>
    </div>
  )
}
