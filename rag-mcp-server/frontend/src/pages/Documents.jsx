import React, { useState, useEffect, useRef } from 'react'
import {
  uploadDocument, listDocuments, deleteDocument,
  reindexCollection, listCollections, createCollection, deleteCollection
} from '../services/api'

export default function Documents() {
  const [collections, setCollections] = useState([])
  const [activeCollection, setActiveCollection] = useState('default')
  const [documents, setDocuments] = useState([])
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [newCollection, setNewCollection] = useState('')
  const [dragActive, setDragActive] = useState(false)
  const fileInputRef = useRef()

  const refresh = async () => {
    setLoading(true)
    try {
      const [colRes, docRes] = await Promise.all([
        listCollections(),
        listDocuments(activeCollection),
      ])
      setCollections(colRes.collections)
      setDocuments(docRes.documents)
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
    setLoading(false)
  }

  useEffect(() => { refresh() }, [activeCollection])

  const handleUpload = async (files) => {
    setLoading(true)
    let uploaded = 0
    for (const file of files) {
      try {
        await uploadDocument(file, activeCollection)
        uploaded++
      } catch (e) {
        setMessage({ type: 'error', text: `Failed to upload ${file.name}: ${e.message}` })
      }
    }
    if (uploaded > 0) {
      setMessage({ type: 'success', text: `Uploaded ${uploaded} file(s)` })
    }
    await refresh()
  }

  const handleDelete = async (filename) => {
    if (!confirm(`Delete "${filename}" from ${activeCollection}?`)) return
    try {
      await deleteDocument(filename, activeCollection)
      setMessage({ type: 'success', text: `Deleted ${filename}` })
      await refresh()
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
  }

  const handleReindex = async () => {
    setLoading(true)
    try {
      const result = await reindexCollection(activeCollection)
      setMessage({ type: 'success', text: `Reindexed: ${result.files_processed} files, ${result.total_chunks} chunks` })
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
    setLoading(false)
  }

  const handleCreateCollection = async () => {
    if (!newCollection.trim()) return
    try {
      await createCollection(newCollection.trim())
      setNewCollection('')
      setMessage({ type: 'success', text: `Created collection: ${newCollection}` })
      await refresh()
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
  }

  const handleDeleteCollection = async (name) => {
    if (!confirm(`Delete collection "${name}" and all its documents?`)) return
    try {
      await deleteCollection(name)
      if (activeCollection === name) setActiveCollection('default')
      setMessage({ type: 'success', text: `Deleted collection: ${name}` })
      await refresh()
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
  }

  const onDrop = (e) => {
    e.preventDefault()
    setDragActive(false)
    if (e.dataTransfer.files.length) handleUpload(Array.from(e.dataTransfer.files))
  }

  return (
    <div className={loading ? 'loading' : ''}>
      <div className="page-header">
        <h2>Documents</h2>
        <p>Manage document collections and upload files for RAG indexing</p>
      </div>

      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
          <button onClick={() => setMessage(null)} style={{ float: 'right', background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }}>x</button>
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <h3>Collections</h3>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <input
              className="input"
              placeholder="New collection name"
              value={newCollection}
              onChange={e => setNewCollection(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleCreateCollection()}
              style={{ width: '200px' }}
            />
            <button className="btn btn-primary btn-sm" onClick={handleCreateCollection}>Create</button>
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          {collections.map(c => (
            <div key={c.name} style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              <button
                className={`btn btn-sm ${activeCollection === c.name ? 'btn-primary' : 'btn-outline'}`}
                onClick={() => setActiveCollection(c.name)}
              >
                {c.name} ({c.document_count})
              </button>
              {c.name !== 'default' && (
                <button className="btn btn-sm btn-danger" onClick={() => handleDeleteCollection(c.name)} title="Delete collection">x</button>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Upload to "{activeCollection}"</h3>
          <button className="btn btn-outline btn-sm" onClick={handleReindex}>Re-index All</button>
        </div>

        <div
          className={`upload-zone ${dragActive ? 'active' : ''}`}
          onDrop={onDrop}
          onDragOver={e => { e.preventDefault(); setDragActive(true) }}
          onDragLeave={() => setDragActive(false)}
          onClick={() => fileInputRef.current?.click()}
        >
          <p style={{ marginBottom: '0.5rem' }}>Drop files here or click to browse</p>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
            Supports: TXT, MD, PDF, DOCX, XLSX, JSON, YAML, code files, and more
          </p>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            style={{ display: 'none' }}
            onChange={e => handleUpload(Array.from(e.target.files))}
          />
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Documents in "{activeCollection}" ({documents.length})</h3>
          <button className="btn btn-outline btn-sm" onClick={refresh}>Refresh</button>
        </div>
        {documents.length > 0 ? (
          <table className="table">
            <thead><tr><th>Filename</th><th style={{ width: '100px' }}>Actions</th></tr></thead>
            <tbody>
              {documents.map(doc => (
                <tr key={doc}>
                  <td>{doc}</td>
                  <td>
                    <button className="btn btn-danger btn-sm" onClick={() => handleDelete(doc)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p style={{ color: 'var(--text-muted)' }}>No documents in this collection.</p>
        )}
      </div>
    </div>
  )
}
