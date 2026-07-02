import React from 'react'

/**
 * Renders LLM answer text with inline [n] citation markers turned into
 * clickable superscripts. Out-of-range or absent markers render as plain
 * text, so answers without citations degrade gracefully.
 *
 *   <AnswerText text={answer} sources={sources} onCite={(idx) => ...} />
 */
export default function AnswerText({ text, sources = [], onCite }) {
  if (!text) return null
  const parts = text.split(/(\[\d+\])/g)
  return (
    <span style={{ whiteSpace: 'pre-wrap' }}>
      {parts.map((part, i) => {
        const m = /^\[(\d+)\]$/.exec(part)
        if (m) {
          const n = parseInt(m[1], 10)
          if (n >= 1 && n <= sources.length) {
            return (
              <sup key={i}>
                <button
                  className="cite-marker"
                  title={sources[n - 1]?.source || ''}
                  onClick={() => onCite?.(n - 1)}
                >
                  {n}
                </button>
              </sup>
            )
          }
        }
        return <span key={i}>{part}</span>
      })}
    </span>
  )
}
