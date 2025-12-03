package com.proboscis.doeff.ide

import com.google.gson.Gson
import com.google.gson.JsonSyntaxException
import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.project.Project
import java.io.File
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit
import kotlin.jvm.Volatile

class IndexerClient(private val project: Project) {
    private val log = Logger.getInstance(IndexerClient::class.java)
    private val gson = Gson()
    @Volatile
    private var cachedIndexerPath: String? = null
    @Volatile
    private var lastExecutedCommand: String? = null

    fun queryEntries(typeArgument: String?, onSuccess: (List<IndexEntry>) -> Unit) {
        CompletableFuture.supplyAsync { runIndexer(typeArgument) }
            .whenComplete { result, throwable ->
                if (throwable != null) {
                    notifyError("Failed to query doeff-indexer", throwable.message ?: "Unknown error", throwable)
                    log.warn("Error while querying doeff-indexer", throwable)
                    return@whenComplete
                }
                if (result == null) {
                    notifyError("doeff-indexer returned no data", "Unexpected empty response", null)
                    return@whenComplete
                }
                ApplicationManager.getApplication().invokeLater {
                    onSuccess(result)
                }
            }
    }
    
    fun findInterpreters(typeArgument: String?, onSuccess: (List<IndexEntry>) -> Unit) {
        findInterpreters(typeArgument, null, 0, onSuccess)
    }

    fun findInterpreters(
        typeArgument: String?,
        proximityFile: String?,
        proximityLine: Int,
        onSuccess: (List<IndexEntry>) -> Unit
    ) {
        CompletableFuture.supplyAsync { runIndexerCommand("find-interpreters", typeArgument, proximityFile, proximityLine) }
            .whenComplete { result, throwable ->
                if (throwable != null) {
                    notifyError("Failed to find interpreters", throwable.message ?: "Unknown error", throwable)
                    log.warn("Error while finding interpreters", throwable)
                    return@whenComplete
                }
                if (result == null) {
                    notifyError("doeff-indexer returned no data", "Unexpected empty response", null)
                    return@whenComplete
                }
                ApplicationManager.getApplication().invokeLater {
                    onSuccess(result)
                }
            }
    }
    
    fun findTransforms(typeArgument: String?, onSuccess: (List<IndexEntry>) -> Unit) {
        findTransforms(typeArgument, null, 0, onSuccess)
    }

    fun findTransforms(
        typeArgument: String?,
        proximityFile: String?,
        proximityLine: Int,
        onSuccess: (List<IndexEntry>) -> Unit
    ) {
        CompletableFuture.supplyAsync { runIndexerCommand("find-transforms", typeArgument, proximityFile, proximityLine) }
            .whenComplete { result, throwable ->
                if (throwable != null) {
                    notifyError("Failed to find transforms", throwable.message ?: "Unknown error", throwable)
                    log.warn("Error while finding transforms", throwable)
                    return@whenComplete
                }
                if (result == null) {
                    notifyError("doeff-indexer returned no data", "Unexpected empty response", null)
                    return@whenComplete
                }
                ApplicationManager.getApplication().invokeLater {
                    onSuccess(result)
                }
            }
    }
    
    fun findKleisli(typeArgument: String?, onSuccess: (List<IndexEntry>) -> Unit) {
        findKleisli(typeArgument, null, 0, onSuccess)
    }

    fun findKleisli(
        typeArgument: String?,
        proximityFile: String?,
        proximityLine: Int,
        onSuccess: (List<IndexEntry>) -> Unit
    ) {
        CompletableFuture.supplyAsync { runIndexerCommand("find-kleisli", typeArgument, proximityFile, proximityLine) }
            .whenComplete { result, throwable ->
                if (throwable != null) {
                    notifyError("Failed to find Kleisli functions", throwable.message ?: "Unknown error", throwable)
                    log.warn("Error while finding Kleisli functions", throwable)
                    return@whenComplete
                }
                if (result == null) {
                    notifyError("doeff-indexer returned no data", "Unexpected empty response", null)
                    return@whenComplete
                }
                ApplicationManager.getApplication().invokeLater {
                    onSuccess(result)
                }
            }
    }

    /**
     * Find all symbols in a specific file using the indexer.
     * Returns the correct module path for symbols in that file.
     */
    fun findSymbolsByFile(filePath: String, onSuccess: (List<IndexEntry>) -> Unit) {
        CompletableFuture.supplyAsync { runIndexerWithFile(filePath) }
            .whenComplete { result, throwable ->
                if (throwable != null) {
                    notifyError("Failed to query symbols by file", throwable.message ?: "Unknown error", throwable)
                    log.warn("Error while querying symbols by file", throwable)
                    return@whenComplete
                }
                if (result == null) {
                    notifyError("doeff-indexer returned no data", "Unexpected empty response", null)
                    return@whenComplete
                }
                ApplicationManager.getApplication().invokeLater {
                    onSuccess(result)
                }
            }
    }

    private fun runIndexerCommand(
        command: String,
        typeArgument: String?,
        proximityFile: String? = null,
        proximityLine: Int = 0
    ): List<IndexEntry>? {
        val indexerPath = resolveIndexerPath() ?: return emptyList()
        val root = project.basePath ?: return emptyList()
        val commandList = mutableListOf(indexerPath, command, "--root", root)
        val trimmedType = typeArgument?.trim()?.takeUnless { it.equals("Any", ignoreCase = true) }
        val supportsTypeArg = when (command) {
            "find-kleisli", "find-interceptors" -> true
            else -> false
        }

        if (supportsTypeArg && !trimmedType.isNullOrEmpty()) {
            commandList.add("--type-arg")
            commandList.add(trimmedType)
        } else if (!supportsTypeArg && !trimmedType.isNullOrEmpty()) {
            log.debug("Skipping type-arg '$trimmedType' for command $command – not supported")
        }

        // Add proximity sorting parameters if provided
        if (!proximityFile.isNullOrEmpty()) {
            commandList.add("--proximity-file")
            commandList.add(proximityFile)
            if (proximityLine > 0) {
                commandList.add("--proximity-line")
                commandList.add(proximityLine.toString())
            }
        }

        log.debug("Executing doeff-indexer: ${commandList.joinToString(" ")}")
        val process = ProcessBuilder(commandList)
            .directory(File(root))
            .start()

        // Read output and error streams concurrently to avoid deadlock
        val output = StringBuilder()
        val error = StringBuilder()
        
        // Create threads to read streams
        val outputReader = Thread {
            process.inputStream.bufferedReader().use { reader ->
                output.append(reader.readText())
            }
        }
        val errorReader = Thread {
            process.errorStream.bufferedReader().use { reader ->
                error.append(reader.readText())
            }
        }
        
        // Start reading streams
        outputReader.start()
        errorReader.start()
        
        // Wait for process to complete
        val completed = process.waitFor(30, TimeUnit.SECONDS)
        
        // Wait for readers to finish
        outputReader.join(1000)
        errorReader.join(1000)

        val stdout = output.toString()
        val stderr = error.toString()

        if (!completed) {
            process.destroyForcibly()
            notifyTranscript(commandList, null, stdout, stderr, "timeout after 30s", isError = true)
            return emptyList()
        }

        val exitCode = process.exitValue()
        if (exitCode != 0) {
            notifyTranscript(commandList, exitCode, stdout, stderr, "exit code $exitCode", isError = true)
            return emptyList()
        }

        val entries = parseEntries(stdout, stderr)
        return if (entries != null) {
            notifyTranscript(commandList, exitCode, stdout, stderr, "success (${entries.size} entries)", isError = false)
            entries
        } else {
            notifyTranscript(commandList, exitCode, stdout, stderr, "invalid JSON output", isError = true)
            emptyList()
        }
    }
    
    private fun runIndexer(typeArgument: String?): List<IndexEntry>? {
        val indexerPath = resolveIndexerPath() ?: return emptyList()
        val root = project.basePath ?: return emptyList()
        val command = mutableListOf(indexerPath, "--root", root, "--kind", "any")
        val trimmedType = typeArgument?.trim()?.takeUnless { it.equals("Any", ignoreCase = true) }
        if (!trimmedType.isNullOrEmpty()) {
            command.add("--type-arg")
            command.add(trimmedType)
        }

        log.debug("Executing doeff-indexer: ${command.joinToString(" ")}")
        val process = ProcessBuilder(command)
            .directory(File(root))
            .start()

        // Read output and error streams concurrently to avoid deadlock
        val output = StringBuilder()
        val error = StringBuilder()

        // Create threads to read streams
        val outputReader = Thread {
            process.inputStream.bufferedReader().use { reader ->
                output.append(reader.readText())
            }
        }
        val errorReader = Thread {
            process.errorStream.bufferedReader().use { reader ->
                error.append(reader.readText())
            }
        }

        // Start reading streams
        outputReader.start()
        errorReader.start()

        // Wait for process to complete
        val completed = process.waitFor(30, TimeUnit.SECONDS)

        // Wait for readers to finish
        outputReader.join(1000)
        errorReader.join(1000)

        val stdout = output.toString()
        val stderr = error.toString()

        if (!completed) {
            process.destroyForcibly()
            notifyTranscript(command, null, stdout, stderr, "timeout after 30s", isError = true)
            return emptyList()
        }

        val exitCode = process.exitValue()
        if (exitCode != 0) {
            notifyTranscript(command, exitCode, stdout, stderr, "exit code $exitCode", isError = true)
            return emptyList()
        }

        val entries = parseEntries(stdout, stderr)
        return if (entries != null) {
            notifyTranscript(command, exitCode, stdout, stderr, "success (${entries.size} entries)", isError = false)
            entries
        } else {
            notifyTranscript(command, exitCode, stdout, stderr, "invalid JSON output", isError = true)
            emptyList()
        }
    }

    private fun runIndexerWithFile(filePath: String): List<IndexEntry>? {
        val indexerPath = resolveIndexerPath() ?: return emptyList()
        val root = project.basePath ?: return emptyList()
        val command = mutableListOf(indexerPath, "index", "--root", root, "--file", filePath)

        lastExecutedCommand = command.joinToString(" ")
        log.debug("Executing doeff-indexer: ${command.joinToString(" ")}")
        val process = ProcessBuilder(command)
            .directory(File(root))
            .start()

        // Read output and error streams concurrently to avoid deadlock
        val output = StringBuilder()
        val error = StringBuilder()

        // Create threads to read streams
        val outputReader = Thread {
            process.inputStream.bufferedReader().use { reader ->
                output.append(reader.readText())
            }
        }
        val errorReader = Thread {
            process.errorStream.bufferedReader().use { reader ->
                error.append(reader.readText())
            }
        }

        // Start reading streams
        outputReader.start()
        errorReader.start()

        // Wait for process to complete
        val completed = process.waitFor(30, TimeUnit.SECONDS)

        // Wait for readers to finish
        outputReader.join(1000)
        errorReader.join(1000)

        val stdout = output.toString()
        val stderr = error.toString()

        if (!completed) {
            process.destroyForcibly()
            notifyTranscript(command, null, stdout, stderr, "timeout after 30s", isError = true)
            return emptyList()
        }

        val exitCode = process.exitValue()
        if (exitCode != 0) {
            notifyTranscript(command, exitCode, stdout, stderr, "exit code $exitCode", isError = true)
            return emptyList()
        }

        val entries = parseEntries(stdout, stderr)
        return if (entries != null) {
            notifyTranscript(command, exitCode, stdout, stderr, "success (${entries.size} entries)", isError = false)
            entries
        } else {
            notifyTranscript(command, exitCode, stdout, stderr, "invalid JSON output", isError = true)
            emptyList()
        }
    }

    private fun parseEntries(stdout: String, stderr: String): List<IndexEntry>? {
        return try {
            val payload = gson.fromJson(stdout, IndexPayload::class.java)
            payload.entries
        } catch (ex: JsonSyntaxException) {
            log.warn("Failed to parse indexer output: $stdout", ex)
            null
        }
    }

    private fun notifyTranscript(
        command: List<String>,
        exitCode: Int?,
        stdout: String,
        stderr: String,
        status: String,
        isError: Boolean
    ) {
        val message = buildString {
            append("Command: ").append(command.joinToString(" "))
            append("\nStatus: ").append(status)
            exitCode?.let { append("\nExit code: ").append(it) }
            if (stderr.isNotBlank()) {
                append("\nstderr:\n").append(stderr)
            }
            if (stdout.isNotBlank()) {
                append("\nstdout:\n").append(stdout)
            }
        }

        if (isError) {
            ProgramPluginDiagnostics.error(project, message)
        } else {
            ProgramPluginDiagnostics.info(project, message)
        }
    }

    fun lastKnownIndexerPath(): String? = cachedIndexerPath

    fun lastExecutedCommand(): String? = lastExecutedCommand

    private fun resolveIndexerPath(): String? {
        cachedIndexerPath?.let { cached ->
            if (File(cached).canExecute()) {
                return cached
            }
        }

        val located = locateIndexerInternal()
        cachedIndexerPath = located
        return located
    }

    private fun locateIndexerInternal(): String? {
        System.getenv("DOEFF_INDEXER_PATH")?.takeIf { it.isNotBlank() }?.let { path ->
            val file = File(path)
            if (file.canExecute()) {
                return file.absolutePath
            }
        }

        val candidates = listOf(
            "/usr/local/bin/doeff-indexer",
            "/usr/bin/doeff-indexer",
            "${System.getProperty("user.home")}/.cargo/bin/doeff-indexer",
            "${System.getProperty("user.home")}/.local/bin/doeff-indexer",
            "/opt/homebrew/bin/doeff-indexer"
        )

        return candidates.firstOrNull { File(it).canExecute() }
            ?: run {
                notifyError(
                    "doeff-indexer not found",
                    "Install with 'cargo install --path packages/doeff-indexer' or set DOEFF_INDEXER_PATH",
                    null
                )
                null
            }
    }

    private fun notifyError(title: String, message: String, throwable: Throwable?) {
        ProgramPluginDiagnostics.error(
            project,
            "$title: $message",
            throwable = throwable,
            key = "indexer-error-$title"
        )
    }
}
