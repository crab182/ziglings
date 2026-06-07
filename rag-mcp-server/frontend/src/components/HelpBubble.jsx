import React, { useState } from 'react'

/**
 * Inline contextual help. Renders a small "?" badge that reveals a tooltip
 * on hover or focus. Keyboard-accessible and screen-reader friendly.
 *
 *   <HelpBubble text="What this field does" />
 */
export default function HelpBubble({ text, title }) {
  const [open, setOpen] = useState(false)

  return (
    <span className="help-bubble">
      <button
        type="button"
        className="help-bubble-trigger"
        aria-label={title || 'Help'}
        aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => { e.preventDefault(); setOpen(o => !o) }}
      >
        ?
      </button>
      {open && (
        <span className="help-bubble-popover" role="tooltip">
          {title && <strong className="help-bubble-title">{title}</strong>}
          <span>{text}</span>
        </span>
      )}
    </span>
  )
}
