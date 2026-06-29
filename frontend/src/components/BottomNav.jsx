const TABS = [
  { key: "projects", label: "Proyectos", icon: "📁" },
  { key: "editor",   label: "Editor",    icon: "🎬" },
  { key: "preview",  label: "Preview",   icon: "▶" },
]

export default function BottomNav({ active, onTab, hasProject }) {
  return (
    <nav className="fixed bottom-0 inset-x-0 z-40 flex border-t border-gray-200 bg-white md:hidden">
      {TABS.map(tab => {
        const disabled = tab.key !== "projects" && !hasProject
        return (
          <button
            key={tab.key}
            onClick={() => !disabled && onTab(tab.key)}
            className={`flex-1 flex flex-col items-center justify-center min-h-[56px] gap-0.5 text-xs transition-colors
              ${active === tab.key ? "text-[#0C447C] font-medium" : "text-gray-400"}
              ${disabled ? "opacity-40 cursor-default" : "cursor-pointer active:bg-gray-50"}`}
          >
            <span className="text-lg leading-none">{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        )
      })}
    </nav>
  )
}
