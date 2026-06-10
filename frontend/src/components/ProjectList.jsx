const STATUS_COLOR = {
  ready: "#639922", pending: "#EF9F27", empty: "#aaa", error: "#E24B4A"
}

export default function ProjectList({ projects, selected, onSelect, onNew, onDeleted }) {
  const handleDelete = async (e, id) => {
    e.stopPropagation()
    if (!confirm("¿Eliminar este proyecto?")) return
    await fetch(`/api/projects/${id}`, { method: "DELETE" })
    onDeleted(id)
  }

  return (
    <aside style={{
      width: 260, borderRight: "0.5px solid var(--color-border-tertiary, #e0e0e0)",
      background: "var(--color-background-primary, #fff)",
      display: "flex", flexDirection: "column",
    }}>
      <div style={{
        padding: "16px", borderBottom: "0.5px solid var(--color-border-tertiary, #e0e0e0)",
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <span style={{ fontWeight: 500, fontSize: 15 }}>Mundial 2026</span>
        <button onClick={onNew} style={{
          padding: "4px 10px", borderRadius: 6, border: "0.5px solid #ccc",
          background: "transparent", cursor: "pointer", fontSize: 13,
        }}>+ Nuevo</button>
      </div>
      <div style={{ flex: 1, overflowY: "auto" }}>
        {projects.length === 0 && (
          <p style={{ padding: 16, fontSize: 13, color: "#999", margin: 0 }}>Sin proyectos aún</p>
        )}
        {projects.map(p => {
          const layerStatuses = Object.values(p.layers || {})
          const allReady = layerStatuses.filter(s => s === "ready").length
          const isSelected = selected?.id === p.id
          return (
            <div key={p.id} onClick={() => onSelect(p)} style={{
              padding: "12px 16px", cursor: "pointer",
              borderBottom: "0.5px solid var(--color-border-tertiary, #e0e0e0)",
              background: isSelected ? "var(--color-background-secondary, #f5f5f3)" : "transparent",
              display: "flex", flexDirection: "column", gap: 4,
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <span style={{ fontSize: 13, fontWeight: 500, lineHeight: 1.3 }}>{p.title}</span>
                <button onClick={(e) => handleDelete(e, p.id)} style={{
                  background: "none", border: "none", cursor: "pointer",
                  fontSize: 12, color: "#bbb", padding: "0 2px",
                }}>✕</button>
              </div>
              {p.match_date && (
                <span style={{ fontSize: 11, color: "#999" }}>{p.match_date}</span>
              )}
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 2 }}>
                {Object.entries(p.layers || {}).map(([layer, status]) => (
                  <span key={layer} style={{
                    width: 8, height: 8, borderRadius: "50%",
                    background: STATUS_COLOR[status] || "#aaa",
                    display: "inline-block",
                    title: layer,
                  }} title={`${layer}: ${status}`} />
                ))}
                <span style={{ fontSize: 11, color: "#999", marginLeft: 2 }}>
                  {allReady}/5 capas
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </aside>
  )
}
