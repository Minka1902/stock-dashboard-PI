import App from './App.jsx'
import AuthGate from './components/AuthGate.jsx'
import SettingsProvider from './context/SettingsContext.jsx'
import useAuth from './hooks/useAuth.js'

export default function Root() {
  const auth = useAuth()
  // App (and every data hook inside it) mounts only once authenticated,
  // so the initial load never fires a burst of 401s. The settings provider
  // sits inside the gate for the same reason — it fetches company names.
  return (
    <AuthGate auth={auth}>
      <SettingsProvider>
        <App auth={auth} />
      </SettingsProvider>
    </AuthGate>
  )
}
