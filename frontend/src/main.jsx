import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Root from './Root.jsx'
import { migrateHashUrl } from './lib/nav'

// Rewrite legacy #/stock/TICKER links to /stock/TICKER before the first render,
// so bookmarks and still-open tabs from the hash era land on their stock rather
// than silently falling back to the dashboard.
migrateHashUrl()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
