// LX04 2.42.118 / 2.42.206：两版 Launcher APK 完全相同；过滤官方回答，保留显式 TTS 与唤醒。
// Runtime only: unloading this script restores every original method.
'use strict';
const counts = { ready: false, requests: 0, syntheses: 0, blockedPcmBytes: 0,
    allowedPcmBytes: 0, blockedUrls: 0, asrResults: 0, nativeInstructions: 0,
    nativeSpeakInstructions: 0, nativeFinished: 0 };
const blocked = new Map();
let pcmBlocked = false;
function isBlocked(id) { return id !== null && blocked.has(String(id)); }
Java.perform(function () {
    const Wrapper = Java.use('com.xiaomi.micolauncher.common.speech.utils.MiAivsWrapper');
    const Helper = Java.use('com.xiaomi.micolauncher.common.speech.SpeechContextHelper');
    const Context = Java.use('com.xiaomi.ai.api.common.Context');
    const Control = Java.use('com.xiaomi.ai.api.Execution$RequestControl');
    const Type = Java.use('com.xiaomi.ai.api.Execution$RequestControlType');
    const Synth = Java.use('com.xiaomi.micolauncher.common.speech.capability.SpeechSynthesizerCapabilityImpl');
    const post = Wrapper.postEvent.overload('com.xiaomi.ai.api.common.Event');
    post.implementation = function (event) {
        const name = String(event.getFullName());
        if (name === 'SpeechRecognizer.Recognize' || name === 'Nlp.Request') {
            const now = Date.now();
            for (const [id, time] of blocked) if (now - time > 600000) blocked.delete(id);
            blocked.set(String(event.getId()), now);
            const contexts = event.getContexts();
            let found = false;
            for (let i = 0; i < contexts.size(); i++) {
                const context = Java.cast(contexts.get(i), Context);
                if (String(context.getFullName()) === 'Execution.RequestControl') {
                    const control = Java.cast(context.getPayload(), Control);
                    const disabled = control.getDisabled();
                    if (!disabled.contains(Type.TTS.value)) disabled.add(Type.TTS.value);
                    found = true;
                }
            }
            if (!found) event.addContext(Helper.requestControlContext(true, false));
            counts.requests++;
            send({event: 'native-answer-disabled', kind: name});
        } else if (name === 'SpeechSynthesizer.Synthesize') {
            counts.syntheses++;
            send({event: 'explicit-tts-allowed'});
        }
        return post.call(this, event);
    };
    // Defensive PCM filter if a server still sends audio for a disabled request.
    const start = Synth.onPlayStart.overload('int', 'java.lang.String');
    start.implementation = function (rate, id) {
        pcmBlocked = isBlocked(id);
        return start.call(this, rate, id);
    };
    const data = Synth.onPcmData.overload('[B');
    data.implementation = function (bytes) {
        if (pcmBlocked) {
            counts.blockedPcmBytes += bytes.length;
            return data.call(this, Java.array('byte', new Array(bytes.length).fill(0)));
        }
        counts.allowedPcmBytes += bytes.length;
        return data.call(this, bytes);
    };
    // URL-based replies use a separate operation instead of the PCM callback.
    const Speak = Java.use('com.xiaomi.micolauncher.instruciton.impl.SpeechSynthesizerSpeakOperation');
    const State = Java.use('com.xiaomi.micolauncher.instruciton.base.BaseOperation$OpState');
    const process = Speak.onProcess.overload('boolean');
    process.implementation = function (async) {
        if (isBlocked(this.getDialogId())) {
            counts.blockedUrls++;
            return State.STATE_SUCCESS.value;
        }
        return process.call(this, async);
    };
    const URLPlayer = Java.use('com.xiaomi.micolauncher.common.player.TtsUrlPlayer');
    const urlPlay = URLPlayer.requestPlay.overload('com.xiaomi.micolauncher.common.player.TtsPlayer$TtsPayload');
    urlPlay.implementation = function (payload) {
        if (payload !== null && isBlocked(payload.id.value)) {
            counts.blockedUrls++;
            // Complete the existing player lifecycle without starting a URL.
            this.d.value = payload;
            this.c.overload().call(this);
            return;
        }
        return urlPlay.call(this, payload);
    };
    const Instruction = Java.use('com.xiaomi.micolauncher.common.speech.capability.InstructionCapabilityImpl');
    // The main protocol entry is observed only; no recognition data is logged.
    const Util = Java.use('com.xiaomi.micolauncher.instruciton.base.InstructionUtil');
    const receive = Instruction.process.overload('com.xiaomi.ai.api.common.Instruction');
    receive.implementation = function (instruction) {
        const name = String(instruction.getFullName());
        if (name === 'SpeechRecognizer.RecognizeResult') counts.asrResults++;
        if (isBlocked(Util.getDialogId(instruction))) {
            counts.nativeInstructions++;
            if (name === 'SpeechSynthesizer.Speak') counts.nativeSpeakInstructions++;
            if (name === 'Dialog.Finish') counts.nativeFinished++;
        }
        return receive.call(this, instruction);
    };
    counts.ready = true;
    send({event: 'ready'});
});
rpc.exports = { stats: function () { return counts; } };
