import React, { useState, useEffect } from 'react'

const STORAGE_KEY = 'pixel-devices'

const DEFAULT_DEVICES = [
  {
    id: 'pixel-primary',
    name: 'My Pixel 10 Pro',
    model: 'Pixel 10 Pro',
    role: 'Primary controller',
    color: 'Obsidian',
    storage: '256 GB',
    androidVersion: 'Android 16',
    tailscaleIp: '',
    status: 'online',
    lastSeen: 'Just now',
    googleAccount: '',
  },
  {
    id: 'pixel-secondary',
    name: 'Backup Pixel 10 Pro',
    model: 'Pixel 10 Pro',
    role: 'Secondary device',
    color: 'Porcelain',
    storage: '512 GB',
    androidVersion: 'Android 16',
    tailscaleIp: '',
    status: 'offline',
    lastSeen: '2 hours ago',
    googleAccount: '',
  },
]

const PIXEL_ICON = (
  <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
    <rect x="6" y="2" width="12" height="20" rx="2.5" stroke="currentColor" strokeWidth="1.5"/>
    <circle cx="12" cy="18.5" r="0.8" fill="currentColor"/>
    <rect x="8.5" y="5" width="7" height="10" rx="1" fill="currentColor" opacity="0.15"/>
  </svg>
)

const GOOGLE_ICON = (
  <svg width="18" height="18" viewBox="0 0 24 24">
    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
  </svg>
)

function loadDevices() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) return JSON.parse(saved)
  } catch {}
  return DEFAULT_DEVICES
}

function saveDevices(devices) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(devices))
}

function DeviceTile({ device, onClick, isSelected }) {
  const statusColor = device.status === 'online' ? '#4cd964' : '#8e8e93'
  return (
    <button className={`win-tile ${isSelected ? 'selected' : ''}`} onClick={onClick}>
      <div className="win-tile-icon">{PIXEL_ICON}</div>
      <div className="win-tile-body">
        <div className="win-tile-title">{device.name}</div>
        <div className="win-tile-sub">{device.model} &middot; {device.role}</div>
        <div className="win-tile-status">
          <span className="win-status-dot" style={{ background: statusColor }} />
          {device.status === 'online' ? 'Connected' : `Offline · ${device.lastSeen}`}
        </div>
      </div>
      <div className="win-tile-chevron">&rsaquo;</div>
    </button>
  )
}

function DeviceDetails({ device, onSave, onRemove, onCancel }) {
  const [form, setForm] = useState({ ...device })

  const update = (key, val) => setForm(f => ({ ...f, [key]: val }))

  return (
    <div className="win-details">
      <div className="win-details-header">
        <div className="win-details-icon">{PIXEL_ICON}</div>
        <div>
          <h3 className="win-details-title">{form.name || 'Unnamed device'}</h3>
          <p className="win-details-sub">{form.model}</p>
        </div>
      </div>

      <div className="win-section">
        <div className="win-section-label">Device info</div>

        <div className="win-row">
          <label className="win-row-label">Device name</label>
          <input className="win-input" value={form.name} onChange={e => update('name', e.target.value)} />
        </div>

        <div className="win-row">
          <label className="win-row-label">Role</label>
          <select className="win-input" value={form.role} onChange={e => update('role', e.target.value)}>
            <option>Primary controller</option>
            <option>Secondary device</option>
            <option>Backup device</option>
            <option>Test device</option>
          </select>
        </div>

        <div className="win-row">
          <label className="win-row-label">Model</label>
          <select className="win-input" value={form.model} onChange={e => update('model', e.target.value)}>
            <option>Pixel 10 Pro</option>
            <option>Pixel 10 Pro XL</option>
            <option>Pixel 10 Pro Fold</option>
            <option>Pixel 10</option>
          </select>
        </div>

        <div className="win-row-grid">
          <div>
            <label className="win-row-label">Color</label>
            <select className="win-input" value={form.color} onChange={e => update('color', e.target.value)}>
              <option>Obsidian</option>
              <option>Porcelain</option>
              <option>Bay</option>
              <option>Sterling</option>
            </select>
          </div>
          <div>
            <label className="win-row-label">Storage</label>
            <select className="win-input" value={form.storage} onChange={e => update('storage', e.target.value)}>
              <option>128 GB</option>
              <option>256 GB</option>
              <option>512 GB</option>
              <option>1 TB</option>
            </select>
          </div>
        </div>

        <div className="win-row">
          <label className="win-row-label">Android version</label>
          <input className="win-input" value={form.androidVersion} onChange={e => update('androidVersion', e.target.value)} />
        </div>
      </div>

      <div className="win-section">
        <div className="win-section-label">Connectivity</div>
        <div className="win-row">
          <label className="win-row-label">Tailscale IP</label>
          <input className="win-input" placeholder="100.x.y.z" value={form.tailscaleIp} onChange={e => update('tailscaleIp', e.target.value)} />
        </div>
      </div>

      <div className="win-section">
        <div className="win-section-label">Google account</div>
        {form.googleAccount ? (
          <div className="win-account-row">
            <div className="win-account-info">
              <div className="win-account-email">{form.googleAccount}</div>
              <div className="win-account-sub">Connected for backup, Play Store, and Find My Device</div>
            </div>
            <button className="win-btn-secondary" onClick={() => update('googleAccount', '')}>Disconnect</button>
          </div>
        ) : (
          <button
            className="win-google-btn"
            onClick={() => {
              const email = prompt('Sign in with Google\n\nEnter your Google account email:')
              if (email) update('googleAccount', email)
            }}
          >
            {GOOGLE_ICON}
            <span>Sign in with Google</span>
          </button>
        )}
      </div>

      <div className="win-actions">
        <button className="win-btn-primary" onClick={() => onSave(form)}>Save</button>
        <button className="win-btn-secondary" onClick={onCancel}>Cancel</button>
        <button className="win-btn-danger" onClick={() => onRemove(form.id)}>Remove device</button>
      </div>
    </div>
  )
}

export default function Devices() {
  const [devices, setDevices] = useState(loadDevices)
  const [selectedId, setSelectedId] = useState(devices[0]?.id ?? null)

  useEffect(() => { saveDevices(devices) }, [devices])

  const selected = devices.find(d => d.id === selectedId)

  const handleSave = (updated) => {
    setDevices(ds => ds.map(d => d.id === updated.id ? updated : d))
  }

  const handleRemove = (id) => {
    setDevices(ds => ds.filter(d => d.id !== id))
    setSelectedId(devices.find(d => d.id !== id)?.id ?? null)
  }

  const handleAddDevice = () => {
    const id = `pixel-${Date.now()}`
    const newDevice = {
      id,
      name: 'New Pixel 10 Pro',
      model: 'Pixel 10 Pro',
      role: 'Secondary device',
      color: 'Obsidian',
      storage: '256 GB',
      androidVersion: 'Android 16',
      tailscaleIp: '',
      status: 'offline',
      lastSeen: 'Never',
      googleAccount: '',
    }
    setDevices(ds => [...ds, newDevice])
    setSelectedId(id)
  }

  return (
    <div className="win-page">
      <div className="win-page-header">
        <h2 className="win-h1">Bluetooth &amp; devices</h2>
        <p className="win-sub">Manage your Pixel 10 Pro phones and connected hardware</p>
      </div>

      <div className="win-layout">
        <div className="win-left">
          <div className="win-section-label" style={{ paddingLeft: '4px', marginBottom: '8px' }}>
            Your devices ({devices.length})
          </div>
          <div className="win-tile-list">
            {devices.map(d => (
              <DeviceTile
                key={d.id}
                device={d}
                isSelected={d.id === selectedId}
                onClick={() => setSelectedId(d.id)}
              />
            ))}
            <button className="win-add-tile" onClick={handleAddDevice}>
              <span className="win-add-plus">+</span>
              <span>Add a device</span>
            </button>
          </div>
        </div>

        <div className="win-right">
          {selected ? (
            <DeviceDetails
              key={selected.id}
              device={selected}
              onSave={handleSave}
              onRemove={handleRemove}
              onCancel={() => setSelectedId(selected.id)}
            />
          ) : (
            <div className="win-empty">
              <h3>No device selected</h3>
              <p>Pick a device from the list to view details, or add a new one.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
