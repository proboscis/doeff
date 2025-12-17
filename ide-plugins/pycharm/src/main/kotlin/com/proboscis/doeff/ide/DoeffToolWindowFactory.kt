package com.proboscis.doeff.ide

import com.intellij.icons.AllIcons
import com.intellij.openapi.actionSystem.ActionManager
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.DefaultActionGroup
import com.intellij.openapi.fileEditor.OpenFileDescriptor
import com.intellij.openapi.project.DumbAware
import com.intellij.openapi.project.DumbAwareAction
import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.SimpleToolWindowPanel
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.openapi.wm.ToolWindow
import com.intellij.openapi.wm.ToolWindowFactory
import com.intellij.ui.ColoredTreeCellRenderer
import com.intellij.ui.SimpleTextAttributes
import com.intellij.ui.content.ContentFactory
import com.intellij.ui.components.JBScrollPane
import com.intellij.ui.treeStructure.Tree
import java.awt.event.MouseAdapter
import java.awt.event.MouseEvent
import java.nio.file.Path
import java.nio.file.Paths
import javax.swing.Icon
import javax.swing.SwingUtilities
import javax.swing.ToolTipManager
import javax.swing.event.TreeExpansionEvent
import javax.swing.event.TreeWillExpandListener
import javax.swing.tree.DefaultMutableTreeNode
import javax.swing.tree.DefaultTreeModel

class DoeffToolWindowFactory : ToolWindowFactory, DumbAware {
    override fun createToolWindowContent(project: Project, toolWindow: ToolWindow) {
        val panel = DoeffToolWindowPanel(project)
        val content = ContentFactory.getInstance().createContent(panel, "", false)
        toolWindow.contentManager.addContent(content)
    }
}

private class DoeffToolWindowPanel(private val project: Project) : SimpleToolWindowPanel(true, true) {
    private val rootNode = DefaultMutableTreeNode(RootItem)
    private val model = DefaultTreeModel(rootNode)
    private val tree = Tree(model)

    init {
        tree.isRootVisible = false
        tree.showsRootHandles = true
        tree.cellRenderer = DoeffTreeCellRenderer(project)
        ToolTipManager.sharedInstance().registerComponent(tree)

        tree.addMouseListener(object : MouseAdapter() {
            override fun mouseClicked(e: MouseEvent) {
                if (!SwingUtilities.isLeftMouseButton(e) || e.clickCount != 2) {
                    return
                }
                val path = tree.getPathForLocation(e.x, e.y) ?: return
                val node = path.lastPathComponent as? DefaultMutableTreeNode ?: return
                activateNode(node)
            }
        })

        tree.addTreeWillExpandListener(object : TreeWillExpandListener {
            override fun treeWillExpand(event: TreeExpansionEvent) {
                val node = event.path.lastPathComponent as? DefaultMutableTreeNode ?: return
                val item = node.userObject as? DoeffTreeItem ?: return
                if (item is EnvGroupItem && !item.loaded) {
                    item.loaded = true
                    loadEnvChain(item.program, node)
                }
            }

            override fun treeWillCollapse(event: TreeExpansionEvent) = Unit
        })

        setContent(JBScrollPane(tree))
        setToolbar(buildToolbar())
        refreshPrograms()
    }

    private fun buildToolbar() = ActionManager.getInstance()
        .createActionToolbar(
            "doeffToolWindow",
            DefaultActionGroup(RefreshAction()),
            true
        )
        .apply { targetComponent = tree }
        .component

    private inner class RefreshAction : DumbAwareAction(
        "Refresh",
        "Refresh doeff entrypoints",
        AllIcons.Actions.Refresh
    ) {
        override fun actionPerformed(e: AnActionEvent) {
            refreshPrograms()
        }
    }

    private fun refreshPrograms() {
        if (project.isDisposed) {
            return
        }
        rootNode.removeAllChildren()
        rootNode.add(DefaultMutableTreeNode(PlaceholderItem("Loading…")))
        model.reload()

        IndexerClient(project).indexPrograms { entries ->
            if (project.isDisposed) {
                return@indexPrograms
            }

            val programs = entries
                .asSequence()
                .filter { it.itemKind == "assignment" }
                .filter { entry -> entry.typeUsages.any { it.kind == ProgramUsageKind.PROGRAM } }
                .filterNot { it.hasMarker("default") }
                .toList()

            val grouped = programs
                .groupBy { modulePath(it.qualifiedName) }
                .toSortedMap(compareBy { it })

            rootNode.removeAllChildren()
            if (grouped.isEmpty()) {
                rootNode.add(DefaultMutableTreeNode(PlaceholderItem("No Programs found")))
                model.reload()
                return@indexPrograms
            }

            grouped.forEach { (modulePath, moduleEntries) ->
                val moduleNode = DefaultMutableTreeNode(ModuleItem(modulePath))
                moduleEntries
                    .sortedBy { it.name }
                    .forEach { entry ->
                        val typeArg = extractProgramTypeArg(entry)
                        val programItem = ProgramItem(entry, typeArg)
                        val programNode = DefaultMutableTreeNode(programItem)

                        programNode.add(DefaultMutableTreeNode(RunActionItem(programItem, withOptions = false)))
                        programNode.add(DefaultMutableTreeNode(RunActionItem(programItem, withOptions = true)))

                        val envGroupItem = EnvGroupItem(programItem)
                        val envGroupNode = DefaultMutableTreeNode(envGroupItem)
                        envGroupNode.add(DefaultMutableTreeNode(PlaceholderItem("Expand to load")))
                        programNode.add(envGroupNode)

                        moduleNode.add(programNode)
                    }
                rootNode.add(moduleNode)
            }

            model.reload()
        }
    }

    private fun loadEnvChain(program: ProgramItem, envGroupNode: DefaultMutableTreeNode) {
        if (project.isDisposed) {
            return
        }

        envGroupNode.removeAllChildren()
        envGroupNode.add(DefaultMutableTreeNode(PlaceholderItem("Loading env chain…")))
        model.reload(envGroupNode)

        IndexerClient(project).findEnvChain(program.entry.qualifiedName) { result ->
            if (project.isDisposed) {
                return@findEnvChain
            }

            envGroupNode.removeAllChildren()
            val chain = result.envChain
            if (chain.isEmpty()) {
                envGroupNode.add(DefaultMutableTreeNode(PlaceholderItem("No env sources found")))
                model.reload(envGroupNode)
                return@findEnvChain
            }

            chain.forEach { envEntry ->
                val envNode = DefaultMutableTreeNode(EnvSourceItem(envEntry))
                addEnvKeyNodes(envNode, envEntry, chain)
                envGroupNode.add(envNode)
            }

            model.reload(envGroupNode)
        }
    }

    private fun addEnvKeyNodes(
        envNode: DefaultMutableTreeNode,
        envEntry: EnvChainEntry,
        chain: List<EnvChainEntry>,
    ) {
        if (envEntry.keys.isEmpty()) {
            envNode.add(DefaultMutableTreeNode(PlaceholderItem("No keys reported")))
            return
        }

        val keyToLastIndex = buildMap {
            chain.forEachIndexed { index, entry ->
                entry.keys.forEach { key -> put(key, index) }
            }
        }

        envEntry.keys.sorted().forEach { key ->
            val lastIndex = keyToLastIndex[key]
            val isFinal = lastIndex == chain.indexOf(envEntry)
            val overriddenBy = if (!isFinal && lastIndex != null) {
                chain[lastIndex].qualifiedName
            } else {
                null
            }
            val value = envEntry.staticValues?.get(key)
            envNode.add(DefaultMutableTreeNode(EnvKeyItem(key, value, isFinal, overriddenBy)))
        }
    }

    private fun activateNode(node: DefaultMutableTreeNode) {
        val item = node.userObject as? DoeffTreeItem ?: return
        when (item) {
            is ProgramItem -> openFile(item.entry.filePath, item.entry.line)
            is RunActionItem -> ProgramExecutionController.runProgram(project, item.program.entry, item.program.typeArg)
            is EnvSourceItem -> openFile(item.entry.filePath, item.entry.line)
            is EnvKeyItem -> Unit
            else -> Unit
        }
    }

    private fun openFile(filePath: String, line: Int) {
        val virtualFile = LocalFileSystem.getInstance().findFileByPath(resolvePath(filePath).toString())
        if (virtualFile == null) {
            ProgramPluginDiagnostics.warn(
                project,
                "Unable to open file: $filePath",
                key = "open-file-$filePath"
            )
            return
        }
        OpenFileDescriptor(project, virtualFile, (line - 1).coerceAtLeast(0), 0).navigate(true)
    }

    private fun resolvePath(filePath: String): Path {
        val candidate = try {
            Paths.get(filePath)
        } catch (_: Exception) {
            Paths.get(filePath.replace("~", System.getProperty("user.home") ?: "~"))
        }
        if (candidate.isAbsolute) {
            return candidate
        }
        val base = project.basePath ?: return candidate
        return Paths.get(base).resolve(candidate).normalize()
    }

    private fun modulePath(qualifiedName: String): String =
        qualifiedName.substringBeforeLast('.', "")

    private fun extractProgramTypeArg(entry: IndexEntry): String {
        val usage = entry.typeUsages.firstOrNull { it.kind == ProgramUsageKind.PROGRAM }
        val arg = usage?.typeArguments?.firstOrNull()?.trim()
        return arg?.takeIf { it.isNotBlank() } ?: "Any"
    }
}

private sealed interface DoeffTreeItem {
    val presentableText: String
    val icon: Icon?
}

private object RootItem : DoeffTreeItem {
    override val presentableText: String = "doeff Programs"
    override val icon: Icon? = AllIcons.Nodes.Folder
}

private data class ModuleItem(val modulePath: String) : DoeffTreeItem {
    override val presentableText: String = if (modulePath.isBlank()) "(root)" else modulePath
    override val icon: Icon? = AllIcons.Nodes.Package
}

private data class ProgramItem(val entry: IndexEntry, val typeArg: String) : DoeffTreeItem {
    override val presentableText: String = "${entry.name}: Program[$typeArg]"
    override val icon: Icon? = AllIcons.Nodes.Variable
}

private data class RunActionItem(val program: ProgramItem, val withOptions: Boolean) : DoeffTreeItem {
    override val presentableText: String = if (withOptions) "Run (options…)" else "Run"
    override val icon: Icon? = if (withOptions) AllIcons.General.GearPlain else AllIcons.RunConfigurations.TestState.Run
}

private class EnvGroupItem(val program: ProgramItem) : DoeffTreeItem {
    @Volatile
    var loaded: Boolean = false
    override val presentableText: String = "Environment Chain"
    override val icon: Icon? = AllIcons.Nodes.ConfigFolder
}

private data class EnvSourceItem(val entry: EnvChainEntry) : DoeffTreeItem {
    override val presentableText: String = entry.qualifiedName
    override val icon: Icon? = if (entry.isUserConfig) {
        AllIcons.Nodes.HomeFolder
    } else {
        AllIcons.FileTypes.Text
    }
}

private data class EnvKeyItem(
    val key: String,
    val value: com.google.gson.JsonElement?,
    val isFinal: Boolean,
    val overriddenBy: String?,
) : DoeffTreeItem {
    override val presentableText: String
        get() {
            val valueText = value?.toString() ?: "<dynamic>"
            return buildString {
                append(key).append(" = ").append(valueText)
                if (isFinal) {
                    append(" ★")
                } else if (overriddenBy != null) {
                    append(" ↓ overridden by ").append(overriddenBy)
                }
            }
        }

    override val icon: Icon? = AllIcons.Nodes.KeymapTools
}

private data class PlaceholderItem(override val presentableText: String) : DoeffTreeItem {
    override val icon: Icon? = null
}

private class DoeffTreeCellRenderer(private val project: Project) : ColoredTreeCellRenderer() {
    override fun customizeCellRenderer(
        tree: javax.swing.JTree,
        value: Any?,
        selected: Boolean,
        expanded: Boolean,
        leaf: Boolean,
        row: Int,
        hasFocus: Boolean,
    ) {
        val node = value as? DefaultMutableTreeNode ?: return
        val item = node.userObject as? DoeffTreeItem ?: return
        icon = item.icon
        append(item.presentableText, SimpleTextAttributes.REGULAR_ATTRIBUTES)
        toolTipText = tooltipFor(item)
    }

    private fun tooltipFor(item: DoeffTreeItem): String? = when (item) {
        is ProgramItem -> "${relPath(item.entry.filePath)}:${item.entry.line}"
        is EnvSourceItem -> "${relPath(item.entry.filePath)}:${item.entry.line}"
        else -> null
    }

    private fun relPath(path: String): String {
        val base = project.basePath ?: return path
        return try {
            val basePath = Paths.get(base)
            val absolute = Paths.get(path)
            if (absolute.isAbsolute) basePath.relativize(absolute).toString() else path
        } catch (_: Exception) {
            path
        }
    }
}
