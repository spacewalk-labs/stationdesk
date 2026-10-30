import SwiftUI
import AppKit
import Darwin

@main
struct StationDeskSetupApp: App {
    var body: some Scene {
        WindowGroup { SetupView().frame(minWidth: 860, minHeight: 630) }
            .windowStyle(.hiddenTitleBar)
    }
}

@MainActor
final class Setup: ObservableObject {
    @Published var language = "en"
    @Published var step = 0
    @Published var paseo = true
    @Published var busy = false
    @Published var log = ""
    @Published var error = ""
    @Published var runtimeMissing = false
    @Published var prepared = false
    @Published var owner = ""
    @Published var loginURL: URL?
    @Published var readyURL: URL?
    private var lastAction = "check"
    func t(_ en: String, _ ko: String) -> String { language == "ko" ? ko : en }
    var steps: [String] { [t("Welcome", "시작"), t("Prepare", "준비"), t("Apps", "앱"), t("Sign in", "로그인"), t("Install", "설치"), t("Ready", "완료")] }
    func run(_ action: String) {
        guard !busy else { return }
        busy = true; error = ""; lastAction = action
        if action == "check" { runtimeMissing = false; owner = "" }
        if action == "install" { readyURL = nil }
        if action == "login" { loginURL = nil }
        let noPaseo = !paseo
        let resource = Bundle.main.resourceURL!
        Task {
            do {
                let result = try await Task.detached { () async throws -> Int32 in
                    let process = Process()
                    process.executableURL = URL(fileURLWithPath: "/bin/bash")
                    process.arguments = [resource.appendingPathComponent("setup.sh").path, action] + (noPaseo ? ["--no-paseo"] : [])
                    let pipe = Pipe()
                    process.standardOutput = pipe; process.standardError = pipe
                    try process.run()
                    let deadline = DispatchWorkItem {
                        guard process.isRunning else { return }
                        // Snapshot this action’s descendants before stopping its shell.
                        func descendants(_ parent: Int32) -> [Int32] {
                            let listing = Process()
                            let output = Pipe()
                            listing.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
                            listing.arguments = ["-P", String(parent)]
                            listing.standardOutput = output
                            listing.standardError = FileHandle.nullDevice
                            do { try listing.run() } catch { return [] }
                            let data = output.fileHandleForReading.readDataToEndOfFile()
                            listing.waitUntilExit()
                            let children = String(decoding: data, as: UTF8.self).split(whereSeparator: \.isNewline).compactMap { Int32($0) }
                            return children.flatMap { descendants($0) + [$0] }
                        }
                        let children = descendants(process.processIdentifier)
                        for pid in children { kill(pid, SIGTERM) }
                        if process.isRunning { process.terminate() }
                        DispatchQueue.global().asyncAfter(deadline: .now() + 2) {
                            // Reap the action even when OrbStack ignores a polite stop.
                            for pid in children { kill(pid, SIGKILL) }
                            if process.isRunning { kill(process.processIdentifier, SIGKILL) }
                        }
                    }
                    DispatchQueue.global().asyncAfter(deadline: .now() + (action == "check" ? 30 : 1800), execute: deadline)
                    defer { deadline.cancel() }
                    // Retain partial lines as bytes: a read may split a UTF-8 character.
                    var pending = Data()
                    while true {
                        let chunk = pipe.fileHandleForReading.availableData
                        if chunk.isEmpty { break }
                        pending.append(chunk)
                        while let newline = pending.firstIndex(of: 10) {
                            let line = String(decoding: pending[..<newline], as: UTF8.self)
                            pending.removeSubrange(...newline)
                            await MainActor.run { self.consume(line + "\n") }
                        }
                    }
                    if !pending.isEmpty {
                        let line = String(decoding: pending, as: UTF8.self)
                        await MainActor.run { self.consume(line) }
                    }
                    process.waitUntilExit()
                    return process.terminationStatus
                }.value
                if result != 0 { error = t("The command stopped (exit \(result)); it may have timed out. Open OrbStack, see the log, then retry.", "명령이 중단됐습니다 (종료 \(result)). 로그를 확인하고 다시 시도하세요.") }
                else if action == "install" {
                    if readyURL != nil { step = 5 }
                    else { error = t("Installation has not returned a workspace address. Retry to check the result.", "설치에서 작업 공간 주소를 반환하지 않았습니다. 다시 시도해 결과를 확인하세요.") }
                }
                if action == "login", let url = loginURL { NSWorkspace.shared.open(url) }
            } catch { self.error = error.localizedDescription }
            busy = false
        }
    }
    private func consume(_ output: String) {
        log += output
        let line = output.trimmingCharacters(in: .newlines)
        let fields = line.split(separator: "\t", maxSplits: 2, omittingEmptySubsequences: false)
        guard fields.count == 3, fields[0] == "SD_STATUS" else { return }
        let value = String(fields[2])
        switch fields[1] {
        case "runtime_missing": runtimeMissing = value != "false"
        case "needs_prepare": if value == "true" { prepared = false }
        case "prepared": prepared = value == "true"
        case "signed_in": owner = value
        case "needs_login": if value == "true" { owner = "" }
        case "login_url":
            if let url = URL(string: value), url.scheme == "https", url.host == "login.tailscale.com" { loginURL = url }
        case "ready_url":
            if let url = URL(string: value), url.scheme == "https", url.host != nil, url.user == nil, url.password == nil { readyURL = url }
        default: break
        }
    }
    func retry() { run(lastAction) }
}

struct SetupView: View {
    @StateObject private var model = Setup()
    @State private var logOpen = false
    var body: some View {
        HStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 24) {
                HStack(spacing: 10) {
                    asset("stationdesk", size: 36)
                    Text("StationDesk").font(.title3.bold())
                }
                ForEach(0..<6) { n in
                    HStack(spacing: 12) {
                        Text("\(n + 1)").font(.caption).frame(width: 25, height: 25).background(n == model.step ? Color.primary.opacity(0.1) : Color.gray.opacity(0.1)).clipShape(Circle())
                        Text(model.steps[n]).fontWeight(n == model.step ? .semibold : .regular)
                    }.foregroundStyle(n == model.step ? Color.primary : Color.secondary)
                }
                Spacer()
            }.padding(28).frame(width: 205).background(Color.gray.opacity(0.04))
            VStack(alignment: .leading, spacing: 18) {
                HStack {
                    Spacer()
                    Picker("Language / 언어", selection: $model.language) { Text("English").tag("en"); Text("한국어").tag("ko") }.fixedSize().accessibilityLabel("Language / 언어")
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        content
                        if !model.error.isEmpty {
                            Text(model.error).foregroundStyle(.red).textSelection(.enabled)
                            if model.step != 4 { Button(model.t("Retry", "다시 시도")) { model.retry() }.disabled(model.busy) }
                        }
                        if model.busy { ProgressView().controlSize(.small) }
                        if !model.log.isEmpty {
                            DisclosureGroup(model.t("Installation log", "설치 로그"), isExpanded: $logOpen) {
                                ScrollView { Text(model.log).font(.system(size: 11, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(height: 150)
                            }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
                Spacer(minLength: 0)
                Divider()
                HStack {
                    if model.step != 0 && model.step != 5 {
                        Button(model.t("Back", "뒤로")) { model.step -= 1 }.disabled(model.busy)
                    }
                    Spacer()
                    nextButton.buttonStyle(.borderedProminent).tint(Color(nsColor: .labelColor)).foregroundStyle(Color(nsColor: .windowBackgroundColor)).keyboardShortcut(.defaultAction)
                }
            }.padding(36)
        }
        .onChange(of: model.error) { error in if !error.isEmpty { logOpen = true } }
    }
    @ViewBuilder func asset(_ name: String, size: CGFloat = 32) -> some View {
        if let url = Bundle.main.url(forResource: name, withExtension: "png"), let image = NSImage(contentsOf: url) {
            Image(nsImage: image).resizable().interpolation(.high).scaledToFit().frame(width: size, height: size).accessibilityHidden(true)
        }
    }
    func appRow(_ icon: String, _ name: String, _ en: String, _ ko: String) -> some View {
        HStack(spacing: 12) {
            asset(icon)
            VStack(alignment: .leading, spacing: 2) {
                Text(name).fontWeight(.medium)
                Text(model.t(en, ko)).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
    func heading(_ en: String, _ ko: String) -> some View { Text(model.t(en, ko)).font(.system(size: 29, weight: .bold)) }
    func paragraph(_ en: String, _ ko: String) -> some View { Text(model.t(en, ko)).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true) }
    @ViewBuilder var content: some View {
        switch model.step {
        case 0:
            heading("Your own place to work.", "나만의 작업 공간을 시작하세요.")
            paragraph("Install the essentials, connect your account, and open StationDesk in your browser.", "기본 앱을 설치하고 계정을 연결한 뒤 브라우저에서 StationDesk를 여세요.")
            GroupBox(model.t("A Linux workspace on this Mac", "이 맥 안의 Linux 작업 공간")) {
                paragraph("OrbStack keeps StationDesk in its own Linux workspace. Your Mac apps and files stay where they are.", "OrbStack 안에 전용 Linux 작업 공간을 만듭니다. 맥의 앱과 파일은 그대로 유지됩니다.").padding(8)
            }
        case 1:
            heading("Prepare this Mac", "이 맥을 준비하세요")
            paragraph("OrbStack provides the Ubuntu environment StationDesk needs. Install OrbStack from its official website, then open it to finish its own setup.", "OrbStack은 StationDesk에 필요한 Ubuntu 환경을 제공합니다. 공식 사이트에서 설치하고 실행해 초기 설정을 마쳐 주세요.")
            if model.runtimeMissing {
                Text(model.t("OrbStack was not found.", "OrbStack을 찾지 못했습니다."))
                Button(model.t("Download OrbStack ↗", "OrbStack 다운로드 ↗")) { NSWorkspace.shared.open(URL(string: "https://orbstack.dev/download")!) }
            }
            HStack {
                Button(model.t("Open OrbStack", "OrbStack 열기")) { NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications/OrbStack.app")) }
                Button(model.t("Check again", "다시 확인")) { model.run("check") }.disabled(model.busy)
            }
            if model.prepared { Label(model.t("Workspace prepared", "작업 공간 준비 완료"), systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
        case 2:
            heading("The essentials, included.", "기본 앱이 함께 설치됩니다.")
            paragraph("Add more apps later in StationDesk.", "다른 앱은 설치 후 StationDesk에서 추가할 수 있습니다.")
            VStack(alignment: .leading, spacing: 12) {
                appRow("stationdesk", "StationDesk", "Home and apps", "홈과 앱")
                appRow("devterm", "Terminal", "Work in a terminal", "터미널 작업")
                appRow("fileview", "File Viewer", "Browse your files", "파일 탐색")
                appRow("publish", "Publish", "Share your work", "작업물 공유")
                appRow("dev-monitor", "Dev Monitor", "Messages and activity", "메시지와 활동")
            }
            paragraph("Claude Code, Codex, OpenCode, and Antigravity are installed when missing. Sign in to the agents you use after setup.", "Claude Code·Codex·OpenCode·Antigravity가 없으면 함께 설치합니다. 사용할 에이전트는 설치 후 로그인하세요.")
            Divider()
            Toggle(isOn: $model.paseo) {
                HStack(spacing: 12) {
                    asset("paseo")
                    Text(model.t("Paseo · Agent workspace (optional)", "Paseo · 에이전트 작업 공간 (선택)"))
                }
            }
        case 3:
            heading("Connect with Tailscale.", "Tailscale을 연결하세요.")
            paragraph("Sign in on Tailscale’s official page, then return here. StationDesk does not ask for your password or API key.", "Tailscale 공식 페이지에서 로그인한 뒤 돌아오세요. StationDesk는 비밀번호나 API 키를 요구하지 않습니다.")
            if !model.owner.isEmpty { Label(model.owner, systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
            else { Button(model.t("Open Tailscale sign-in ↗", "Tailscale 로그인 열기 ↗")) { model.run("login") }.disabled(model.busy) }
            Button(model.t("Check sign-in", "로그인 확인")) { model.run("check") }.disabled(model.busy)
        case 4:
            heading(model.error.isEmpty ? "Installing StationDesk" : "Installation paused", model.error.isEmpty ? "StationDesk 설치 중" : "설치가 중단됐습니다")
            if model.error.isEmpty { paragraph("Installing your apps and checking your workspace address. This may take a few minutes.", "앱을 설치하고 작업 공간 주소를 확인합니다. 몇 분 정도 걸릴 수 있습니다.") }
        default:
            heading("Your workspace is ready.", "작업 공간이 준비됐습니다.")
            paragraph("Open StationDesk to find your apps and messages.", "StationDesk를 열어 앱과 메시지를 확인하세요.")
            if let url = model.readyURL { Text(url.absoluteString).textSelection(.enabled).font(.system(.body, design: .monospaced)) }
        }
    }
    @ViewBuilder var nextButton: some View {
        switch model.step {
        case 0: Button(model.t("Get started", "시작하기")) { model.step = 1; model.run("check") }
        case 1: Button(model.t(model.prepared ? "Continue" : "Prepare workspace", model.prepared ? "계속" : "작업 공간 준비")) { if model.prepared { model.step = 2 } else { model.run("prepare") } }.disabled(model.busy || model.runtimeMissing)
        case 2: Button(model.t("Continue", "계속")) { model.step = 3; model.run("check") }
        case 3: Button(model.t("Install StationDesk", "StationDesk 설치")) { model.step = 4; model.run("install") }.disabled(model.busy || model.owner.isEmpty)
        case 4: Button(model.t(model.error.isEmpty ? "Installing…" : "Retry", model.error.isEmpty ? "설치 중…" : "다시 시도")) { model.retry() }.disabled(model.busy || model.error.isEmpty)
        default: Button(model.t("Open StationDesk ↗", "StationDesk 열기 ↗")) { if let url = model.readyURL { NSWorkspace.shared.open(url) } }.disabled(model.readyURL == nil)
        }
    }
}
