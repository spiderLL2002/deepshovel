import AppKit
import Combine
import Darwin
import Foundation
import SwiftUI

@_silgen_name("flock")
private func systemFlock(_ descriptor: Int32, _ operation: Int32) -> Int32

// Startup grace applies only before the first ready response in a running session.
// An already-ready service losing readiness is a failure, not a fresh startup.
private enum CompilerState {
    case stopped, starting, ready, failed
}

private struct CompilerStartup {
    private var observedStart: TimeInterval?
    private var processID: Int?
    private var wasReady = false

    mutating func observe(running: Bool, ready: Bool, now: TimeInterval, pid: Int? = nil) -> CompilerState {
        guard running else {
            observedStart = nil
            processID = nil
            wasReady = false
            return .stopped
        }
        if processID != pid {
            observedStart = nil
            wasReady = false
            processID = pid
        }
        if observedStart == nil { observedStart = now }
        if ready {
            wasReady = true
            return .ready
        }
        return !wasReady && now - (observedStart ?? now) < 240 ? .starting : .failed
    }
}

private struct Signals: Sendable {
    let servicePID: Int?
    var serviceAgent: Bool { servicePID != nil }
    let localHealth: Bool
    let compilerHealth: Bool
    let tunnelAgent: Bool
    let publicHealth: Bool
    let watchdogAgent: Bool
    let awakeAgent: Bool
}

private struct FallbackHistory: Decodable {
    let count: Int
    let lastActivatedAt: Date
}

private enum OverallState {
    case checking, online, starting, degraded, stopped, fallbackUsed

    var label: String {
        switch self {
        case .checking: return "正在检查"
        case .online: return "系统运行中"
        case .starting: return "正在启动"
        case .degraded: return "部分服务异常"
        case .stopped: return "系统已停止"
        case .fallbackUsed: return "曾启用 DeepSeek 保底"
        }
    }

    var symbol: String {
        switch self {
        case .checking: return "arrow.triangle.2.circlepath"
        case .online: return "circle.hexagongrid.fill"
        case .starting: return "clock.fill"
        case .degraded: return "exclamationmark.triangle.fill"
        case .stopped: return "stop.circle.fill"
        case .fallbackUsed: return "exclamationmark.triangle.fill"
        }
    }

    var tint: NSColor {
        switch self {
        case .checking, .starting: return .systemOrange
        case .online: return .systemGreen
        case .degraded, .fallbackUsed: return .systemOrange
        case .stopped: return .secondaryLabelColor
        }
    }
}

@MainActor
private final class ControlModel: ObservableObject {
    @Published fileprivate var signals: Signals?
    @Published fileprivate var compilerState: CompilerState?
    @Published fileprivate var state: OverallState = .checking
    @Published fileprivate var checkedAt: Date?
    @Published fileprivate var actionInProgress = false
    @Published fileprivate var fallbackHistory: FallbackHistory?

    fileprivate var refreshAction: () -> Void = {}
    fileprivate var startAction: () -> Void = {}
    fileprivate var stopAction: () -> Void = {}
    fileprivate var restartAction: () -> Void = {}
    fileprivate var openPublicAction: () -> Void = {}
    fileprivate var openLogsAction: () -> Void = {}
    fileprivate var openFolderAction: () -> Void = {}
    fileprivate var quitAction: () -> Void = {}
}

private struct ControlPanel: View {
    @ObservedObject var model: ControlModel

    private var shouldStart: Bool { model.state == .stopped }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider().overlay(Color.white.opacity(0.08))
            statusList
            if let history = model.fallbackHistory, history.count > 0 {
                VStack(alignment: .leading, spacing: 5) {
                    Label("曾启用 DeepSeek 保底", systemImage: "exclamationmark.triangle.fill")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(.orange)
                    Text("GPT 连续 6 次未成功，请检查 GPT 服务。")
                        .font(.system(size: 10))
                    Text("累计 \(history.count) 次 · 最近 \(history.lastActivatedAt.formatted(date: .abbreviated, time: .standard))")
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(12)
                .background(Color.orange.opacity(0.1))
            }
            Divider().overlay(Color.white.opacity(0.08))
            controls
            footer
        }
        .frame(width: 290)
        .background(Color(red: 0.055, green: 0.06, blue: 0.07))
        .preferredColorScheme(.dark)
    }

    private var header: some View {
        HStack(spacing: 8) {
            ZStack {
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .fill(Color(red: 0.07, green: 0.14, blue: 0.29))
                Image(systemName: "circle.hexagongrid.fill")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(Color(red: 0.36, green: 0.72, blue: 1))
            }
            .frame(width: 34, height: 34)

            VStack(alignment: .leading, spacing: 3) {
                Text("DeepShovel 控制台")
                    .font(.system(size: 12, weight: .bold))
                HStack(spacing: 4) {
                    Circle().fill(Color(model.state.tint)).frame(width: 5, height: 5)
                    Text(model.state.label)
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button(action: model.refreshAction) {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 11, weight: .semibold))
                    .frame(width: 24, height: 24)
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .disabled(model.actionInProgress)
            .help("立即刷新")
            .accessibilityLabel("立即刷新服务状态")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
    }

    private var statusList: some View {
        VStack(spacing: 7) {
            statusRow("Web 服务", "127.0.0.1:\(DeploymentConfig.localPort)", "server.rack", model.signals?.localHealth)
            statusRow("Compiler", "127.0.0.1:\(DeploymentConfig.compilerPort)", "cpu", model.signals?.compilerHealth, compilerState: model.compilerState)
            statusRow("Cloudflare Tunnel", DeploymentConfig.tunnelName, "network", model.signals?.tunnelAgent)
            statusRow("公网访问", DeploymentConfig.publicURL.host ?? DeploymentConfig.publicURL.absoluteString, "globe.asia.australia.fill", model.signals?.publicHealth)
            statusRow("后台服务", "macOS 登录后自动启动", "bolt.horizontal.circle", model.signals?.serviceAgent)
            statusRow("自动恢复", "独立检查并恢复公网链路", "cross.case", model.signals?.watchdogAgent)
            statusRow("保持唤醒", "防止空闲休眠中断公网服务", "moon.zzz", model.signals?.awakeAgent)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private func statusRow(_ title: String, _ detail: String, _ symbol: String, _ healthy: Bool?, compilerState: CompilerState? = nil) -> some View {
        let stopped = (model.state == .stopped && healthy == false) || compilerState == .stopped
        let starting = compilerState == .starting
        let pending = !stopped && (healthy == nil || starting)
        let label = stopped ? "已停止" : (starting ? "启动中" : (healthy == nil ? "检查中" : (healthy == true ? "正常" : "异常")))
        return HStack(spacing: 7) {
            Image(systemName: symbol)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.secondary)
                .frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.system(size: 10, weight: .semibold))
                Text(detail)
                    .font(.system(size: 9))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 6)
            HStack(spacing: 3) {
                Image(systemName: stopped ? "stop.circle" : (pending ? "ellipsis.circle" : (healthy == true ? "checkmark.circle.fill" : "xmark.circle.fill")))
                Text(label)
            }
            .font(.system(size: 9, weight: .semibold))
            .foregroundStyle(stopped ? Color.secondary : (pending ? Color.orange : (healthy == true ? Color.green : Color.red)))
        }
        .accessibilityElement(children: .combine)
    }

    private var controls: some View {
        VStack(spacing: 7) {
            HStack(spacing: 7) {
                Button(action: shouldStart ? model.startAction : model.restartAction) {
                    Label(shouldStart ? "启动全部" : "重新启动", systemImage: shouldStart ? "play.fill" : "arrow.clockwise")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(PrimaryControlButtonStyle())

                Button(action: model.stopAction) {
                    Label("停止全部", systemImage: "stop.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(StopControlButtonStyle())
            }
            .disabled(model.actionInProgress)

            HStack(spacing: 7) {
                link("打开网站", "safari", model.openPublicAction)
                link("查看日志", "doc.text.magnifyingglass", model.openLogsAction)
                link("服务目录", "folder", model.openFolderAction)
                Spacer()
                link("退出监控", "power", model.quitAction)
            }
            .font(.system(size: 9, weight: .medium))
        }
        .padding(.horizontal, 12)
        .padding(.top, 8)
    }

    private func link(_ title: String, _ symbol: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
        }
        .buttonStyle(.plain)
        .foregroundStyle(Color(red: 0.29, green: 0.65, blue: 1))
    }

    private var footer: some View {
        HStack {
            Text(model.checkedAt.map { "每 7 秒刷新 · \($0.formatted(date: .omitted, time: .standard))" } ?? "正在首次检查")
            Spacer()
            Text("后台持续运行")
        }
        .font(.system(size: 8, weight: .medium))
        .foregroundStyle(Color.white.opacity(0.42))
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
    }
}

private struct PrimaryControlButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 10, weight: .semibold))
            .padding(.vertical, 6)
            .foregroundStyle(.white)
            .background(Color.blue.opacity(configuration.isPressed ? 0.72 : 1), in: Capsule())
    }
}

private struct StopControlButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 10, weight: .semibold))
            .padding(.vertical, 6)
            .foregroundStyle(Color.red.opacity(configuration.isPressed ? 0.65 : 0.9))
            .background(Color.red.opacity(configuration.isPressed ? 0.14 : 0.08), in: Capsule())
    }
}

@main
@MainActor
final class ServiceControl: NSObject, NSApplicationDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
    private let popover = NSPopover()
    private let model = ControlModel()
    private var timer: Timer?
    private var compilerStartup = CompilerStartup()
    private var refreshing = false
    private var instanceLockDescriptor: Int32 = -1

    static func main() {
        let application = NSApplication.shared
        let delegate = ServiceControl()
        application.delegate = delegate
        application.run()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard acquireInstanceLock() else {
            NSApplication.shared.terminate(nil)
            return
        }
        NSApplication.shared.setActivationPolicy(.accessory)
        configureActions()
        configureStatusItem()
        configurePopover()
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: DeploymentConfig.refreshSeconds, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
        if instanceLockDescriptor >= 0 {
            _ = systemFlock(instanceLockDescriptor, LOCK_UN)
            Darwin.close(instanceLockDescriptor)
            instanceLockDescriptor = -1
        }
    }

    private func acquireInstanceLock() -> Bool {
        let directory = URL(fileURLWithPath: DeploymentConfig.installRoot, isDirectory: true)
            .appendingPathComponent("data", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        } catch {
            return false
        }
        let path = directory.appendingPathComponent("control-instance.lock").path
        let descriptor = path.withCString {
            Darwin.open($0, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
        }
        guard descriptor >= 0 else { return false }
        guard systemFlock(descriptor, LOCK_EX | LOCK_NB) == 0 else {
            Darwin.close(descriptor)
            return false
        }
        instanceLockDescriptor = descriptor
        return true
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showPopover()
        return true
    }

    private func configureActions() {
        model.refreshAction = { [weak self] in self?.refresh() }
        model.startAction = { [weak self] in self?.perform("start") }
        model.stopAction = { [weak self] in self?.perform("stop") }
        model.restartAction = { [weak self] in self?.perform("restart") }
        model.openPublicAction = { NSWorkspace.shared.open(DeploymentConfig.publicURL) }
        model.openLogsAction = { NSWorkspace.shared.open(URL(fileURLWithPath: DeploymentConfig.installRoot + "/logs")) }
        model.openFolderAction = { NSWorkspace.shared.open(URL(fileURLWithPath: DeploymentConfig.installRoot)) }
        model.quitAction = { NSApplication.shared.terminate(nil) }
    }

    private func configureStatusItem() {
        guard let button = statusItem.button else { return }
        button.target = self
        button.action = #selector(togglePopover)
        button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        button.imagePosition = .imageOnly
        updateStatusButton(.checking)
    }

    private func configurePopover() {
        popover.behavior = .transient
        popover.animates = true
        popover.contentSize = NSSize(width: 290, height: 410)
        popover.contentViewController = NSHostingController(rootView: ControlPanel(model: model))
    }

    @objc private func togglePopover() {
        if popover.isShown {
            popover.performClose(nil)
        } else {
            showPopover()
        }
    }

    private func showPopover() {
        guard let button = statusItem.button else { return }
        refresh()
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
    }

    private func updateStatusButton(_ state: OverallState) {
        guard let button = statusItem.button else { return }
        let image = NSImage(systemSymbolName: state.symbol, accessibilityDescription: "DeepShovel \(state.label)")
            ?? NSImage(systemSymbolName: "circle.grid.2x2.fill", accessibilityDescription: "DeepShovel")
        image?.isTemplate = true
        button.image = image
        button.contentTintColor = state.tint
        button.toolTip = "DeepShovel 控制台·\(state.label)"
        button.setAccessibilityLabel("DeepShovel 控制台，\(state.label)")
    }

    private func refresh() {
        guard !refreshing else { return }
        refreshing = true
        if model.signals == nil {
            model.state = .checking
            updateStatusButton(.checking)
        }
        Task {
            async let servicePID = Self.agentPID(DeploymentConfig.serviceLabel)
            async let localHealth = Self.health(DeploymentConfig.localHealthURL, timeout: 2.5)
            async let compilerHealth = Self.health(URL(string: "http://127.0.0.1:\(DeploymentConfig.compilerPort)/ready")!, timeout: 3, compiler: true)
            async let tunnelAgent = Self.agentRunning(DeploymentConfig.tunnelLabel)
            async let publicHealth = Self.health(DeploymentConfig.publicHealthURL, timeout: 5)
            async let watchdogAgent = Self.agentRunning(DeploymentConfig.watchdogLabel)
            async let awakeAgent = Self.agentRunning(DeploymentConfig.awakeLabel)
            let signals = await Signals(
                servicePID: servicePID,
                localHealth: localHealth,
                compilerHealth: compilerHealth,
                tunnelAgent: tunnelAgent,
                publicHealth: publicHealth,
                watchdogAgent: watchdogAgent,
                awakeAgent: awakeAgent
            )
            apply(signals)
            refreshing = false
        }
    }

    private func apply(_ signals: Signals) {
        let historyURL = URL(fileURLWithPath: DeploymentConfig.installRoot + "/data/compiler/model-fallback.json")
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        if let data = try? Data(contentsOf: historyURL),
           let history = try? decoder.decode(FallbackHistory.self, from: data) {
            model.fallbackHistory = history
        }
        popover.contentSize = NSSize(width: 290, height: model.fallbackHistory == nil ? 410 : 505)
        let compilerState = compilerStartup.observe(running: signals.serviceAgent, ready: signals.compilerHealth, now: ProcessInfo.processInfo.systemUptime, pid: signals.servicePID)
        model.compilerState = compilerState
        let state: OverallState
        if signals.serviceAgent && signals.localHealth && signals.compilerHealth && signals.tunnelAgent && signals.publicHealth && signals.watchdogAgent && signals.awakeAgent {
            state = model.fallbackHistory == nil ? .online : .fallbackUsed
        } else if !signals.serviceAgent && !signals.tunnelAgent && !signals.watchdogAgent {
            state = .stopped
        } else if compilerState == .starting || (signals.serviceAgent && signals.tunnelAgent && signals.watchdogAgent && !signals.publicHealth) {
            state = .starting
        } else {
            state = .degraded
        }
        model.signals = signals
        model.state = state
        model.checkedAt = Date()
        updateStatusButton(state)
    }

    private func perform(_ command: String) {
        guard !model.actionInProgress else { return }
        model.actionInProgress = true
        Task {
            let result = await Self.runControl(command)
            model.actionInProgress = false
            if let error = result {
                let alert = NSAlert()
                alert.messageText = "DeepShovel 操作失败"
                alert.informativeText = error
                alert.alertStyle = .warning
                alert.runModal()
            }
            refresh()
        }
    }

    private static func agentRunning(_ label: String) async -> Bool {
        await agentPID(label) != nil
    }

    private static func agentPID(_ label: String) async -> Int? {
        await Task.detached(priority: .utility) {
            let process = Process()
            let pipe = Pipe()
            process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
            process.arguments = ["print", "gui/\(getuid())/\(label)"]
            process.standardOutput = pipe
            process.standardError = FileHandle.nullDevice
            do {
                try process.run()
                process.waitUntilExit()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                let output = String(data: data, encoding: .utf8) ?? ""
                guard process.terminationStatus == 0 && output.contains("state = running") else { return nil }
                for line in output.split(separator: "\n") {
                    let entry = line.trimmingCharacters(in: .whitespaces)
                    if entry.hasPrefix("pid = ") { return Int(entry.dropFirst(6)) }
                }
                return nil
            } catch {
                return nil
            }
        }.value
    }

    private static func health(_ url: URL, timeout: TimeInterval, compiler: Bool = false) async -> Bool {
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: timeout)
        request.httpMethod = "GET"
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { return false }
            guard (200..<300).contains(http.statusCode) else { return false }
            if compiler {
                guard let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let retrieval = body["retrieval"] as? [String: Any] else { return false }
                return body["service"] as? String == "deepshovel-compiler"
                    && body["status"] as? String == "ok"
                    && retrieval["status"] as? String == "ready"
            }
            return true
        } catch {
            return false
        }
    }

    private static func runControl(_ command: String) async -> String? {
        await Task.detached(priority: .userInitiated) {
            let process = Process()
            let pipe = Pipe()
            process.executableURL = URL(fileURLWithPath: DeploymentConfig.controlScript)
            process.arguments = [command]
            process.standardOutput = pipe
            process.standardError = pipe
            do {
                try process.run()
                process.waitUntilExit()
                guard process.terminationStatus != 0 else { return nil }
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                let text = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
                return text?.isEmpty == false ? text : "命令退出码 \(process.terminationStatus)"
            } catch {
                return "无法执行服务控制命令。"
            }
        }.value
    }
}
