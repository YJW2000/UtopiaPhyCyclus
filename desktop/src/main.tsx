import { createRoot } from 'react-dom/client'
import App from './App'
import logo from '../../static/sidebar.webp'
import './shell.css'

try {
  const tokens = JSON.parse(localStorage.getItem('desktop.theme') || '{}')
  for (const [name, value] of Object.entries(tokens))
    document.documentElement.style.setProperty(name, String(value))
  if (typeof tokens['--mc-text'] === 'string')
    void window.utopia.tintWindowControls(tokens['--mc-text']).catch(() => {})
} catch {
  /* A fresh installation starts with the warm skin. */
}
createRoot(document.getElementById('root')!).render(
  <>
    <div className="window-titlebar" aria-hidden="true">
      <div className="window-drag-area">
        <img src={logo} alt="" draggable={false} />
        <span>乌托邦高中物理题库助手</span>
      </div>
    </div>
    <App />
  </>
)
