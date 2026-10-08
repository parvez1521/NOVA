import Foundation
import AVFoundation
import Speech
import CoreAudio
import AudioToolbox

func emit(_ type: String, _ values: [String:Any] = [:]) {
    let message=values.merging(["type":type]) {_,new in new}
    if let data=try? JSONSerialization.data(withJSONObject:message) {FileHandle.standardOutput.write(data);FileHandle.standardOutput.write(Data([10]))}
}
func phraseMatches(_ text: String) -> Bool {
    text.range(of:#"\b(?:hey|hello)[\s,!.]+nova\b"#,options:[.regularExpression,.caseInsensitive]) != nil
}
func wav(_ samples: [Float], _ rate: Double) -> Data {
    let count=Int(Double(samples.count)*16000/rate)
    var pcm=Data(capacity:count*2)
    for index in 0..<count {
        let position=Double(index)*rate/16000,first=min(Int(position),samples.count-1),next=min(first+1,samples.count-1)
        let fraction=Float(position-Double(first));let sample=max(-1,min(1,samples[first]*(1-fraction)+samples[next]*fraction))
        var value=Int16(sample*(sample<0 ? 32768 : 32767)).littleEndian
        withUnsafeBytes(of:&value) {pcm.append(contentsOf:$0)}
    }
    var output=Data("RIFF".utf8)
    func u32(_ value: UInt32) {var value=value.littleEndian;withUnsafeBytes(of:&value) {output.append(contentsOf:$0)}}
    func u16(_ value: UInt16) {var value=value.littleEndian;withUnsafeBytes(of:&value) {output.append(contentsOf:$0)}}
    u32(UInt32(36+pcm.count));output.append(Data("WAVEfmt ".utf8));u32(16);u16(1);u16(1);u32(16000);u32(32000);u16(2);u16(16);output.append(Data("data".utf8));u32(UInt32(pcm.count));output.append(pcm);return output
}

final class VoiceEngine {
    let engine=AVAudioEngine()
    let queue=DispatchQueue(label:"local.nova.audio")
    let recognizer=SFSpeechRecognizer(locale:Locale(identifier:"en-US"))
    var request:SFSpeechAudioBufferRecognitionRequest?
    var recognition:SFSpeechRecognitionTask?
    var mode="off",provider="apple_speech",sensitivity="MEDIUM"
    var samples=[Float](),preRoll=[Float](),rate:Double=48000
    var lastSpeech=Date(),started=Date(),hasSpeech=false,noise:Float=0.004,lastLevel=Date.distantPast
    var lastSpeechSample=0
    var silence:Double=1.2,postRoll:Double=0.25,preRollSeconds:Double=0.35,maxSeconds:Double=60
    var autoStop=true,wakeEnabled=false,stopping=false,acknowledging=false,lastWake=Date.distantPast
    var acknowledgement:Process?

    func start(_ options:[String:Any]) {
        guard !engine.isRunning else {configure(options);emit("ready",["mode":mode,"provider":provider]);return}
        configure(options)
        let status=AVCaptureDevice.authorizationStatus(for:.audio)
        if status == .authorized {startAfterMicrophone(options);return}
        if status != .notDetermined {emit("error",["code":"MIC_PERMISSION_DENIED"]);return}
        AVCaptureDevice.requestAccess(for:.audio) {granted in
            guard granted else {emit("error",["code":"MIC_PERMISSION_DENIED"]);return}
            self.startAfterMicrophone(options)
        }
    }
    func startAfterMicrophone(_ options:[String:Any]) {
        guard wakeEnabled && provider=="apple_speech" else {queue.async {self.begin(options)};return}
        guard recognizer?.supportsOnDeviceRecognition == true else {emit("error",["code":"LOCAL_WAKE_UNAVAILABLE"]);return}
        let status=SFSpeechRecognizer.authorizationStatus()
        if status == .authorized {queue.async {self.begin(options)};return}
        if status != .notDetermined {emit("error",["code":"LOCAL_WAKE_UNAVAILABLE"]);return}
        SFSpeechRecognizer.requestAuthorization {status in
            guard status == .authorized,self.recognizer?.supportsOnDeviceRecognition == true else {emit("error",["code":"LOCAL_WAKE_UNAVAILABLE"]);return}
            self.queue.async {self.begin(options)}
        }
    }
    func configure(_ options:[String:Any]) {
        provider=options["provider"] as? String ?? provider;sensitivity=options["sensitivity"] as? String ?? sensitivity
        silence=Double(options["silence_ms"] as? Int ?? 1200)/1000
        preRollSeconds=Double(options["pre_roll_ms"] as? Int ?? 350)/1000;postRoll=Double(options["post_roll_ms"] as? Int ?? 250)/1000
        maxSeconds=Double(options["max_seconds"] as? Int ?? 60);autoStop=options["auto_stop"] as? Bool ?? true
        wakeEnabled=options["wake"] as? Bool ?? false;mode=wakeEnabled ? "passive" : "capture"
    }
    func selectDevice(_ uid:String) throws {
        if uid.isEmpty {return}
        var address=AudioObjectPropertyAddress(mSelector:kAudioHardwarePropertyDevices,mScope:kAudioObjectPropertyScopeGlobal,mElement:kAudioObjectPropertyElementMain)
        var length:UInt32=0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject),&address,0,nil,&length)==noErr else {throw NSError(domain:"microphone",code:1)}
        var devices=[AudioDeviceID](repeating:0,count:Int(length)/MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject),&address,0,nil,&length,&devices)==noErr else {throw NSError(domain:"microphone",code:1)}
        for var device in devices {
            var property=AudioObjectPropertyAddress(mSelector:kAudioDevicePropertyDeviceUID,mScope:kAudioObjectPropertyScopeGlobal,mElement:kAudioObjectPropertyElementMain)
            var value:Unmanaged<CFString>?=nil;var size=UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            if AudioObjectGetPropertyData(device,&property,0,nil,&size,&value)==noErr && value?.takeUnretainedValue() as String?==uid,let unit=engine.inputNode.audioUnit {
                guard AudioUnitSetProperty(unit,kAudioOutputUnitProperty_CurrentDevice,kAudioUnitScope_Global,0,&device,UInt32(MemoryLayout<AudioDeviceID>.size))==noErr else {throw NSError(domain:"microphone",code:1)}
                return
            }
        }
        throw NSError(domain:"microphone",code:1)
    }
    func begin(_ options:[String:Any]) {
        do {
            stopping=false
            try selectDevice(options["device_id"] as? String ?? "")
            try? engine.inputNode.setVoiceProcessingEnabled(true)
            engine.inputNode.isVoiceProcessingAGCEnabled=options["auto_gain"] as? Bool ?? true
            let format=engine.inputNode.outputFormat(forBus:0);rate=format.sampleRate
            guard rate>0,format.channelCount>0 else {emit("error",["code":"MIC_UNAVAILABLE"]);return}
            engine.inputNode.installTap(onBus:0,bufferSize:1024,format:format) {buffer,_ in
                guard let channels=buffer.floatChannelData else {return}
                let count=Int(buffer.frameLength);var mono=[Float](repeating:0,count:count)
                for channel in 0..<Int(buffer.format.channelCount) {for index in 0..<count {mono[index]+=channels[channel][index]/Float(buffer.format.channelCount)}}
                self.queue.async {self.consume(mono)}
            }
            samples.removeAll();preRoll.removeAll();started=Date();lastSpeech=Date();hasSpeech=false
            engine.prepare();try engine.start();emit("ready",["mode":mode,"provider":provider])
        } catch {emit("error",["code":"MIC_CAPTURE_FAILED"]);stop()}
    }
    func consume(_ chunk:[Float]) {
        guard !stopping else {return}
        let level=sqrt(chunk.reduce(Float(0)) {$0+$1*$1}/Float(max(1,chunk.count))),now=Date()
        let base:Float=sensitivity=="LOW" ? 0.025 : sensitivity=="HIGH" ? 0.008 : 0.015
        let speech=level>max(base,noise*2.6)
        if !speech {noise=noise*0.995+min(level,0.015)*0.005}
        if now.timeIntervalSince(lastLevel)>0.15 {lastLevel=now;emit("level",["level":Double(level),"mode":mode])}
        if mode=="capture" || mode=="followup" {
            if speech && !hasSpeech {
                hasSpeech=true;started=now;samples=preRoll;emit("speech.started",["mode":mode])
                if mode=="followup" {mode="capture"}
            }
            if hasSpeech {samples.append(contentsOf:chunk)}
            if speech {lastSpeech=now;lastSpeechSample=samples.count}
            if hasSpeech && (now.timeIntervalSince(started)>maxSeconds || autoStop && now.timeIntervalSince(lastSpeech)>max(silence,postRoll)) {finishCapture()}
            else if !hasSpeech && now.timeIntervalSince(started)>10 {samples.removeAll();mode=wakeEnabled ? "passive" : "off";emit("timeout");if !wakeEnabled {stop()}}
        } else if mode=="passive" {
            if speech && !hasSpeech {hasSpeech=true;started=now;samples=preRoll;if provider=="apple_speech" {beginRecognition()}}
            if hasSpeech {
                samples.append(contentsOf:chunk)
                if provider=="apple_speech" {appendRecognition(chunk)}
                if speech {lastSpeech=now}
                if now.timeIntervalSince(lastSpeech)>0.45 || now.timeIntervalSince(started)>4 {
                    if provider=="whisper_vad" {emit("keyword.audio",["audio":wav(Array(samples.prefix(Int(rate*4))),rate).base64EncodedString()])}
                    request?.endAudio();recognition?.finish();request=nil;samples.removeAll();hasSpeech=false
                }
            }
        }
        preRoll.append(contentsOf:chunk);let maxCount=Int(rate*preRollSeconds)
        if preRoll.count>maxCount {preRoll.removeFirst(preRoll.count-maxCount)}
    }
    func beginRecognition() {
        recognition?.cancel()
        let request=SFSpeechAudioBufferRecognitionRequest();request.requiresOnDeviceRecognition=true;request.shouldReportPartialResults=true;request.contextualStrings=["Hey Nova","Hello Nova"]
        self.request=request
        recognition=recognizer?.recognitionTask(with:request) {result,_ in
            if let result=result,phraseMatches(result.bestTranscription.formattedString) {
                let minimum:Float=self.sensitivity=="LOW" ? 0.75 : self.sensitivity=="HIGH" ? 0.3 : 0.5
                let confidence=result.bestTranscription.segments.suffix(2).map(\.confidence).min() ?? 0
                if result.isFinal || confidence>=minimum {self.queue.async {self.wake()}}
            }
        }
        appendRecognition(preRoll)
    }
    func appendRecognition(_ chunk:[Float]) {
        guard let request=request,let format=AVAudioFormat(commonFormat:.pcmFormatFloat32,sampleRate:rate,channels:1,interleaved:false),let buffer=AVAudioPCMBuffer(pcmFormat:format,frameCapacity:AVAudioFrameCount(chunk.count)),let channel=buffer.floatChannelData?[0] else {return}
        buffer.frameLength=AVAudioFrameCount(chunk.count);chunk.withUnsafeBufferPointer {pointer in if let base=pointer.baseAddress {channel.update(from:base,count:chunk.count)}}
        request.append(buffer)
    }
    func wake() {
        guard wakeEnabled,mode=="passive",Date().timeIntervalSince(lastWake)>2 else {return}
        lastWake=Date();recognition?.cancel();request=nil;hasSpeech=false;samples.removeAll();mode="ack";started=Date();autoStop=true
        emit("wake.detected",["provider":provider]);acknowledge()
    }
    func acknowledge() {
        acknowledging=true;emit("ack.started",["text":"Yeah?"])
        let process=Process();process.executableURL=URL(fileURLWithPath:"/usr/bin/say");process.arguments=["-r","180","Yeah?"]
        process.terminationHandler={ [weak self] _ in self?.queue.async { self?.finishAcknowledgement() } }
        do { try process.run();acknowledgement=process } catch { finishAcknowledgement() }
    }
    func finishAcknowledgement() {
        guard acknowledging else {return}
        acknowledging=false;acknowledgement=nil;mode="capture";started=Date();emit("ack.completed",["text":"Yeah?"]);emit("ready",["mode":"capture"])
    }
    func finishCapture() {
        let end=min(samples.count,Int(rate*maxSeconds),lastSpeechSample+Int(rate*postRoll))
        let captured=Array(samples.prefix(end));samples.removeAll();hasSpeech=false;lastSpeechSample=0
        mode=wakeEnabled ? "busy" : "off"
        if captured.count>Int(rate*0.15) {emit("audio",["audio":wav(captured,rate).base64EncodedString(),"duration_ms":Int(Double(captured.count)*1000/rate)])}
        else {emit("timeout")}
        if !wakeEnabled {stop()}
    }
    func command(_ options:[String:Any]) {
        switch options["action"] as? String ?? "" {
        case "start":start(options)
        case "capture":recognition?.cancel();request=nil;samples.removeAll();hasSpeech=false;mode="capture";autoStop=options["auto_stop"] as? Bool ?? true;started=Date();emit("ready",["mode":"capture"])
        case "finish":finishCapture()
        case "wake":wake()
        case "followup":mode="followup";started=Date();hasSpeech=false;samples.removeAll();autoStop=true;emit("ready",["mode":mode])
        case "passive":mode=wakeEnabled ? "passive" : "off";hasSpeech=false;samples.removeAll();recognition?.cancel();request=nil;emit("ready",["mode":mode])
        case "speaking":if wakeEnabled {mode="passive";hasSpeech=false;samples.removeAll()}
        case "stop":stop();emit("stopped");exit(0)
        default:emit("error",["code":"VOICE_ACTION_INVALID"])
        }
    }
    func stop() {stopping=true;acknowledging=false;acknowledgement?.terminate();acknowledgement=nil;if engine.isRunning {engine.stop();engine.inputNode.removeTap(onBus:0)};recognition?.cancel();request=nil;samples.removeAll();preRoll.removeAll();mode="off"}
} 

func microphonePermissionState() -> String {
    switch AVCaptureDevice.authorizationStatus(for:.audio) {
    case .authorized:return "GRANTED"
    case .notDetermined:return "NOT_DETERMINED"
    default:return "DENIED"
    }
}
func speechPermissionState(_ recognizer:SFSpeechRecognizer?) -> String {
    guard recognizer?.supportsOnDeviceRecognition == true else {return "UNAVAILABLE"}
    switch SFSpeechRecognizer.authorizationStatus() {
    case .authorized:return "GRANTED"
    case .notDetermined:return "NOT_DETERMINED"
    default:return "DENIED"
    }
}

if CommandLine.arguments.contains("--self-test") {
    assert(phraseMatches("Hey, Nova!"));assert(phraseMatches("Hello Nova"));assert(!phraseMatches("Nova"));assert(!phraseMatches("hey novation"))
    let voice=VoiceEngine();voice.rate=16000;voice.mode="capture";voice.autoStop=false
    voice.preRoll=Array(repeating:Float(0.001),count:5600)
    let tone=(0..<16000).map {Float(sin(Double($0)*0.1))*0.08}
    voice.consume(tone);voice.finishCapture()
    emit("self-test",["phrase_matching":true,"pre_roll_samples":5600,"pcm_rate":16000,"raw_audio_saved":false])
} else if CommandLine.arguments.contains("--status") {
    let recognizer=SFSpeechRecognizer(locale:Locale(identifier:"en-US"))
    let devices=AVCaptureDevice.DiscoverySession(deviceTypes:[.microphone],mediaType:.audio,position:.unspecified).devices
    emit("status",["apple_speech_available":recognizer?.supportsOnDeviceRecognition == true,"microphone_permission":AVCaptureDevice.authorizationStatus(for:.audio).rawValue,"speech_permission":SFSpeechRecognizer.authorizationStatus().rawValue,"microphone_state":microphonePermissionState(),"speech_recognition_state":speechPermissionState(recognizer),"devices":devices.map {["id":$0.uniqueID,"name":$0.localizedName]}])
} else {
    let voice=VoiceEngine()
    DispatchQueue.global().async {
        while let line=readLine() {if let data=line.data(using:.utf8),let options=try? JSONSerialization.jsonObject(with:data) as? [String:Any] {voice.queue.async {voice.command(options)}}}
        voice.queue.async {voice.stop();exit(0)}
    }
    RunLoop.main.run()
}
