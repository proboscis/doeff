package com.proboscis.doeff.ide

import com.intellij.openapi.project.Project
import com.intellij.openapi.ui.ComboBox
import com.intellij.openapi.ui.DialogWrapper
import java.awt.BorderLayout
import java.awt.Component
import java.awt.GridBagConstraints
import java.awt.GridBagLayout
import java.awt.Insets
import javax.swing.DefaultComboBoxModel
import javax.swing.JComponent
import javax.swing.JLabel
import javax.swing.JPanel

class ProgramSelectionDialog(
    project: Project,
    private val programPath: String,
    private val programType: String,
    private val indexerPath: String?,
    private val interpreters: List<IndexEntry>,
    private val kleisliPrograms: List<IndexEntry>,
    private val transformers: List<IndexEntry>
) : DialogWrapper(project, true) {

    private val interpreterModel = DefaultComboBoxModel(interpreters.toTypedArray())
    private val kleisliModel = DefaultComboBoxModel<IndexEntry?>()
    private val transformerModel = DefaultComboBoxModel<IndexEntry?>()

    private val interpreterCombo = ComboBox(interpreterModel)
    private val kleisliCombo = ComboBox(kleisliModel)
    private val transformerCombo = ComboBox(transformerModel)

    init {
        title = "Run doeff Program"
        kleisliModel.addElement(null)
        kleisliPrograms.forEach { kleisliModel.addElement(it) }
        transformerModel.addElement(null)
        transformers.forEach { transformerModel.addElement(it) }
        interpreterCombo.renderer = ProgramEntryRenderer("Select interpreter")
        kleisliCombo.renderer = ProgramEntryRenderer("No Kleisli")
        transformerCombo.renderer = ProgramEntryRenderer("No transformer")
        init()
    }

    override fun createCenterPanel(): JComponent {
        val panel = JPanel(BorderLayout())
        val content = JPanel(GridBagLayout())
        val constraints = GridBagConstraints().apply {
            fill = GridBagConstraints.HORIZONTAL
            weightx = 1.0
            insets = Insets(4, 4, 4, 4)
        }

        var row = 0
        constraints.gridx = 0
        constraints.gridy = row
        content.add(JLabel("Program"), constraints)
        constraints.gridx = 1
        content.add(JLabel(programPath), constraints)

        row += 1
        constraints.gridx = 0
        constraints.gridy = row
        content.add(JLabel("Program type"), constraints)
        constraints.gridx = 1
        content.add(JLabel(programType), constraints)

        row += 1
        constraints.gridx = 0
        constraints.gridy = row
        content.add(JLabel("Indexer binary"), constraints)
        constraints.gridx = 1
        val indexerLabel = JLabel(indexerPath ?: "<unknown>")
        indexerLabel.toolTipText = indexerPath ?: "Indexer location not resolved"
        content.add(indexerLabel, constraints)

        row += 1
        constraints.gridx = 0
        constraints.gridy = row
        content.add(JLabel("Interpreter"), constraints)
        constraints.gridx = 1
        content.add(interpreterCombo, constraints)

        row += 1
        constraints.gridx = 0
        constraints.gridy = row
        content.add(JLabel("Kleisli"), constraints)
        constraints.gridx = 1
        content.add(kleisliCombo, constraints)

        row += 1
        constraints.gridx = 0
        constraints.gridy = row
        content.add(JLabel("Transformer"), constraints)
        constraints.gridx = 1
        content.add(transformerCombo, constraints)

        panel.add(content, BorderLayout.CENTER)
        return panel
    }

    override fun doOKAction() {
        if (interpreterCombo.selectedItem == null) {
            return
        }
        super.doOKAction()
    }

    fun buildSelection(): RunConfigurationSelection? {
        val interpreter = interpreterCombo.selectedItem as? IndexEntry ?: return null
        val kleisli = kleisliCombo.selectedItem as? IndexEntry
        val transformer = transformerCombo.selectedItem as? IndexEntry
        return RunConfigurationSelection(
            programPath = programPath,
            programType = programType,
            interpreter = interpreter,
            kleisli = kleisli,
            transformer = transformer
        )
    }
}

private class ProgramEntryRenderer(private val emptyText: String) : javax.swing.DefaultListCellRenderer() {
    override fun getListCellRendererComponent(
        list: javax.swing.JList<*>?,
        value: Any?,
        index: Int,
        isSelected: Boolean,
        cellHasFocus: Boolean
    ): Component {
        val label = super.getListCellRendererComponent(list, value, index, isSelected, cellHasFocus) as JLabel
        val entry = value as? IndexEntry
        if (entry == null) {
            label.text = "<none>"
            label.toolTipText = emptyText
        } else {
            label.text = "${entry.name} (${entry.qualifiedName})"
            label.toolTipText = entry.docstring ?: entry.qualifiedName
        }
        return label
    }
}

data class RunConfigurationSelection(
    val programPath: String,
    val programType: String,
    val interpreter: IndexEntry,
    val kleisli: IndexEntry?,
    val transformer: IndexEntry?
)
