import { useState, useEffect } from "react"
import ProjectList from "./components/ProjectList"
import ProjectEditor from "./components/ProjectEditor"
import NewProjectModal from "./components/NewProjectModal"
import BottomNav from "./components/BottomNav"

export default function App() {
  const [projects, setProjects] = useState([])
  const [selected, setSelected] = useState(null)
  const [showNew, setShowNew] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [mobileTab, setMobileTab] = useState("projects")

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
    setMobileTab("editor")
    setDrawerOpen(false)
  }

  const handleDeleted = (id) => {
    setProjects(prev => prev.filter(p => p.id !== id))
    if (selected?.id === id) setSelected(null)
  }

  const handleSelect = (p) => {
    setSelected(p)
    setMobileTab("editor")
    setDrawerOpen(false)
  }

  const handleTab = (tab) => {
    setMobileTab(tab)
    if (tab === "projects") setDrawerOpen(true)
    else setDrawerOpen(false)
  }

  return (
    <div className="flex h-[100dvh] bg-gray-50 text-gray-900 font-sans">
      {/* Overlay mobile */}
      {drawerOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-30 md:hidden"
          onClick={() => setDrawerOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside className={`
        fixed inset-y-0 left-0 z-40 w-[280px] transform transition-transform duration-200
        md:relative md:translate-x-0 md:w-[260px] md:z-auto
        ${drawerOpen ? "translate-x-0" : "-translate-x-full"}
      `}>
        <ProjectList
          projects={projects}
          selected={selected}
          onSelect={handleSelect}
          onNew={() => setShowNew(true)}
          onDeleted={handleDeleted}
          onRefresh={fetchProjects}
        />
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto pb-16 md:pb-0">
        {selected ? (
          <ProjectEditor
            project={selected}
            onRefresh={fetchProjects}
            onMenuOpen={() => setDrawerOpen(true)}
            mobileTab={mobileTab}
          />
        ) : (
          <EmptyState onNew={() => setShowNew(true)} />
        )}
      </main>

      {/* Bottom nav mobile */}
      <BottomNav
        active={drawerOpen ? "projects" : mobileTab}
        onTab={handleTab}
        hasProject={!!selected}
      />

      {showNew && (
        <NewProjectModal onCreated={handleCreated} onClose={() => setShowNew(false)} />
      )}
    </div>
  )
}

function EmptyState({ onNew }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-gray-400">
      <div className="text-5xl">⚽</div>
      <p className="text-base">Selecciona un proyecto o crea uno nuevo</p>
      <button
        onClick={onNew}
        className="px-5 py-2 rounded-lg border border-gray-300 bg-transparent cursor-pointer text-sm text-gray-600 hover:bg-gray-100 transition-colors"
      >
        + Nuevo vídeo
      </button>
    </div>
  )
}
