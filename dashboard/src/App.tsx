import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Chamber } from './pages/Chamber'
import { Portfolio } from './pages/Portfolio'
import { Trades } from './pages/Trades'
import { Scenarios } from './pages/Scenarios'
import { Recon } from './pages/Recon'
import { Config } from './pages/Config'

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/"          element={<Chamber />} />
        <Route path="/portfolio" element={<Portfolio />} />
        <Route path="/trades"    element={<Trades />} />
        <Route path="/scenarios" element={<Scenarios />} />
        <Route path="/recon"     element={<Recon />} />
        <Route path="/config"    element={<Config />} />
      </Routes>
    </Layout>
  )
}
