import { useState, useEffect } from "react"
import ProjectList from "./components/ProjectList"
import ProjectEditor from "./components/ProjectEditor"
import NewProjectModal from "./components/NewProjectModal"

export default function App() {
  const [projects, setProjects] = useState([])
  const [selected, setSelected] = useState(null)
  const [showNew, setShowNew] = useState(false)

  const fetchProjects = async () => {
    const res = await fetch("/api/projects/")
    const data = await res.json()
    setProjects(data)
  }

  useEffect(() => { fetchProjects() }, [])

  const handleCreated = (project) => {
    setProjects(prev => [project, ...prev])
    setSelected(project)
    setShowNew(false)
  }

  const handleDeleted = (id) => {
    setProjects(prev => prev.filter(p => p.id !== id))
    if (selected?.id === id) setSelected(null)
  }

  return (
    <div style={{
      display: "flex", height: "100vh", fontFamily: "var(--font-sans, system-ui)",
      background: "var(--color-background-tertiary, #f5f5f3)",
      color: "var(--color-text-primary, #1a1a1a)",
    }}>
      <ProjectList
        projects={projects}
        selected={selected}
        onSelect={setSelected}
        onNew={() => setShowNew(true)}
        onDeleted={handleDeleted}
      />
      <main style={{ flex: 1, overflowY: "auto" }}>
        {selected
          ? <ProjectEditor project={selected} onRefresh={fetchProjects} />
          : <EmptyState onNew={() => setShowNew(true)} />
        }
      </main>
      {showNew && (
        <NewProjectModal onCreated={handleCreated} onClose={() => setShowNew(false)} />
      )}
    </div>
  )
}

function EmptyState({ onNew }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center",
      justifyContent: "center", height: "100%", gap: 16,
      color: "var(--color-text-secondary, #666)",
    }}>
      <div style={{ fontSize: 48 }}>⚽</div>
      <p style={{ fontSize: 16, margin: 0 }}>Selecciona un proyecto o crea uno nuevo</p>
      <button onClick={onNew} style={{
        padding: "8px 20px", borderRadius: 8, border: "0.5px solid #ccc",
        background: "transparent", cursor: "pointer", fontSize: 14,
      }}>
        + Nuevo vídeo
      </button>
    </div>
  )
}
