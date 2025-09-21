package com.proboscis.doeff.ide

import com.intellij.execution.ProgramRunnerUtil
import com.intellij.execution.RunManager
import com.intellij.execution.RunnerAndConfigurationSettings
import com.intellij.execution.configurations.ConfigurationFactory
import com.intellij.execution.executors.DefaultRunExecutor
import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import com.intellij.openapi.projectRoots.Sdk
import com.intellij.openapi.roots.ProjectRootManager
import com.intellij.openapi.module.ModuleManager
import com.jetbrains.python.run.PythonConfigurationType
import com.jetbrains.python.run.PythonRunConfiguration
import com.jetbrains.python.sdk.PythonSdkUtil

class DoEffRunConfigurationHelper(private val project: Project) {
    private val logger = Logger.getInstance(DoEffRunConfigurationHelper::class.java)

    fun run(selection: RunConfigurationSelection) {
        val primaryModule = ModuleManager.getInstance(project).modules.firstOrNull()
        val sdk = primaryModule?.let { PythonSdkUtil.findPythonSdk(it) } ?: findSdkFromRoots()
        if (sdk == null) {
            notify("Python SDK not configured")
            return
        }

        val runManager = RunManager.getInstance(project)
        val configurationType = PythonConfigurationType.getInstance()
        val factory: ConfigurationFactory = configurationType.configurationFactories.first()
        val configurationName = buildConfigurationName(selection)

        val settings = runManager.allSettings.firstOrNull { it.name == configurationName }
            ?: createSettings(runManager, factory, configurationName)
        val configuration = settings.configuration as PythonRunConfiguration
        configure(configuration, sdk, selection)

        if (!runManager.allSettings.contains(settings)) {
            runManager.addConfiguration(settings)
        }
        runManager.selectedConfiguration = settings
        logger.debug("Executing doeff run configuration: ${configuration.scriptName} ${configuration.scriptParameters}")
        ProgramRunnerUtil.executeConfiguration(settings, DefaultRunExecutor.getRunExecutorInstance())
    }

    private fun configure(configuration: PythonRunConfiguration, sdk: Sdk, selection: RunConfigurationSelection) {
        configuration.sdk = sdk
        configuration.module = ModuleManager.getInstance(project).modules.firstOrNull()
        configuration.isModuleMode = true
        configuration.scriptName = "doeff"
        configuration.scriptParameters = buildScriptParameters(selection)
        configuration.workingDirectory = project.basePath
        val pythonPath = project.basePath ?: ""
        configuration.envs = mapOf("PYTHONPATH" to pythonPath)
        configuration.isPassParentEnvs = true
    }

    private fun buildScriptParameters(selection: RunConfigurationSelection): String {
        val arguments = mutableListOf(
            "run",
            "--program",
            selection.programPath,
            "--interpreter",
            selection.interpreter.qualifiedName
        )
        selection.kleisli?.let {
            arguments.add("--apply")
            arguments.add(it.qualifiedName)
        }
        selection.transformer?.let {
            arguments.add("--transform")
            arguments.add(it.qualifiedName)
        }
        arguments.add("--format")
        arguments.add("json")
        return arguments.joinToString(separator = " ")
    }

    private fun buildConfigurationName(selection: RunConfigurationSelection): String = buildString {
        append(selection.programPath)
        append(" :: ")
        append(selection.interpreter.qualifiedName)
        selection.kleisli?.let {
            append(" | kleisli=")
            append(it.qualifiedName)
        }
        selection.transformer?.let {
            append(" | transform=")
            append(it.qualifiedName)
        }
    }

    private fun createSettings(
        runManager: RunManager,
        factory: ConfigurationFactory,
        name: String
    ): RunnerAndConfigurationSettings {
        val settings = runManager.createConfiguration(name, factory)
        settings.isTemporary = false
        return settings
    }

    private fun findSdkFromRoots(): Sdk? {
        val roots = ProjectRootManager.getInstance(project).contentRoots
        for (root in roots) {
            PythonSdkUtil.findSdkByPath(root.path)?.let { return it }
        }
        return null
    }

    private fun notify(message: String) {
        logger.warn(message)
        ApplicationManager.getApplication().invokeLater {
            NotificationGroupManager.getInstance()
                .getNotificationGroup("doeff.plugin")
                .createNotification("doeff", message, NotificationType.ERROR)
                .notify(project)
        }
    }
}
