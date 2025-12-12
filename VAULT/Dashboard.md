---
title: Project Dashboard
created: 2025-12-04
updated: 2025-12-04
tags: [dashboard]
---

# Project Dashboard

```dataviewjs
const projects = dv.pages('"Projects"');
const tasks = dv.pages('"Tasks"');
const issues = dv.pages('"Issues"');

const activeProjects = projects.filter(p => p.status === "in-progress").length;
const totalTasks = tasks.length;
const doneTasks = tasks.filter(t => t.status === "done").length;
const todoTasks = tasks.filter(t => t.status === "todo").length;
const openIssues = issues.filter(i => i.status === "open").length;

const card = (label, value, color) => `
<div style="background:${color}15;border:1px solid ${color}40;border-radius:8px;padding:16px 24px;text-align:center;min-width:120px;">
  <div style="font-size:28px;font-weight:700;color:${color};">${value}</div>
  <div style="font-size:12px;color:#9ca3af;margin-top:4px;">${label}</div>
</div>`;

dv.paragraph(`<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:24px;">
  ${card("Active Projects", activeProjects, "#3b82f6")}
  ${card("Tasks Done", doneTasks, "#22c55e")}
  ${card("Tasks Todo", todoTasks, "#eab308")}
  ${card("Open Issues", openIssues, "#ef4444")}
</div>`);
```

## Projects Overview

```dataviewjs
const projects = dv.pages('"Projects"');
const tasks = dv.pages('"Tasks"');

const statusColor = (status) => {
  const colors = {
    "done": "#22c55e",
    "in-progress": "#3b82f6",
    "planned": "#a855f7",
    "blocked": "#ef4444"
  };
  return colors[status] || "#6b7280";
};

const progressBar = (done, total) => {
  if (total === 0) return `<span style="color:#6b7280;">-</span>`;
  const pct = Math.round(done / total * 100);
  const color = pct >= 80 ? "#22c55e" : pct >= 50 ? "#eab308" : "#f97316";
  return `<div style="display:flex;align-items:center;gap:8px;">
    <div style="width:100px;height:8px;background:#374151;border-radius:4px;overflow:hidden;">
      <div style="width:${pct}%;height:100%;background:${color};"></div>
    </div>
    <span style="color:${color};font-weight:600;">${pct}%</span>
  </div>`;
};

const statusBadge = (status) => {
  const color = statusColor(status);
  return `<span style="background:${color}22;color:${color};padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;">${status}</span>`;
};

const emptyCell = `<span style="color:#6b7280;">-</span>`;

const rows = projects.map(p => {
  const projectTasks = tasks.filter(t => t["related-project"] === p.id);
  const done = projectTasks.filter(t => t.status === "done").length;
  const total = projectTasks.length;
  
  return [
    p.file.link,
    p.title,
    statusBadge(p.status),
    `${done} / ${total}`,
    progressBar(done, total),
    p.owner || emptyCell,
    p["target-date"] || emptyCell
  ];
});

dv.table(["ID", "Name", "Status", "Tasks", "Progress", "Owner", "Target"], rows);
```

## Open Issues by Project

```dataview
TABLE WITHOUT ID
  related-project as "Project",
  length(filter(rows, (r) => r.status = "open")) as "Open",
  length(filter(rows, (r) => r.status = "resolved")) as "Resolved",
  length(rows) as "Total"
FROM "Issues"
WHERE related-project != null
GROUP BY related-project
SORT related-project ASC
```

---

## Todo Tasks (All Projects)

```dataviewjs
const tasks = dv.pages('"Tasks"').where(t => t.status === "todo");

const emptyCell = `<span style="color:#6b7280;">-</span>`;

const priorityBadge = (priority) => {
  const colors = {
    "high": "#ef4444",
    "medium": "#eab308",
    "low": "#22c55e"
  };
  const color = colors[priority] || "#6b7280";
  return `<span style="background:${color}22;color:${color};padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;">${priority || "-"}</span>`;
};

const rows = tasks.sort(t => t.priority === "high" ? 0 : t.priority === "medium" ? 1 : 2)
  .map(t => [
    t.file.link,
    t.title,
    t["related-project"] || emptyCell,
    priorityBadge(t.priority)
  ]);

dv.table(["ID", "Task", "Project", "Priority"], rows);
```

## Recently Completed

```dataview
TABLE WITHOUT ID
  link(file.link, id) as "ID",
  title as "Task",
  related-project as "Project",
  updated as "Completed"
FROM "Tasks"
WHERE status = "done"
SORT updated DESC
LIMIT 10
```

## Open Issues

```dataviewjs
const issues = dv.pages('"Issues"').where(i => i.status === "open");

const emptyCell = `<span style="color:#6b7280;">-</span>`;

const severityBadge = (severity) => {
  const colors = {
    "critical": "#dc2626",
    "high": "#ef4444",
    "medium": "#eab308",
    "low": "#22c55e"
  };
  const color = colors[severity] || "#6b7280";
  return `<span style="background:${color}22;color:${color};padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600;">${severity || "-"}</span>`;
};

const rows = issues.sort(i => i.severity === "critical" ? 0 : i.severity === "high" ? 1 : i.severity === "medium" ? 2 : 3)
  .map(i => [
    i.file.link,
    i.title,
    i["related-project"] || emptyCell,
    severityBadge(i.severity)
  ]);

dv.table(["ID", "Issue", "Project", "Severity"], rows);
```


