import queue
import threading
import time
import customtkinter as ctk
import tkinter as tk

ctk.set_appearance_mode("System")  
ctk.set_default_color_theme("blue") 

class QuickCommandWindow:
    def __init__(self) -> None:
        self.root = ctk.CTk()
        self.root.title("CUA-Lark")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self._result_queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._running = False

        self.status_var = ctk.StringVar(value="🟢 空闲")
        self.model_var = ctk.StringVar(value="gpt-5.4-mini")

        self._build_ui()
        self._place_window()
        self.root.after(120, self._poll_result_queue)

    def _build_ui(self) -> None:
        frame = ctk.CTkFrame(self.root, fg_color="transparent")
        frame.pack(padx=20, pady=20, fill="both", expand=True)

        # 输入框
        self.input_entry = ctk.CTkEntry(
            frame, 
            width=260,
            height=36,
            corner_radius=8,
            border_width=1,
            placeholder_text="请输入飞书指令"
        )
        self.input_entry.grid(row=0, column=0, padx=(0, 12), pady=(0, 10), sticky="ew")
        self.input_entry.bind("<Return>", self._on_submit)

        # 麦克风按钮 (通过悬浮覆盖的方式放置在输入框内部右侧)
        self.mic_button = ctk.CTkButton(
            self.input_entry,  # 注意：将父组件设为 input_entry，方便相对定位
            text="🎙️",       # 麦克风图标
            width=28,
            height=28,
            corner_radius=6,
            fg_color="transparent",               # 透明背景使其融入输入框
            hover_color=("gray85", "gray25"),     # 鼠标悬停时稍微变色
            text_color=("gray40", "gray60"),
            command=self._on_mic_click            # 绑定语音点击事件
        )
        # relx=1.0靠右对齐，rely=0.5垂直居中，anchor="e"右锚点，x=-4向左偏移4像素留出边距
        self.mic_button.place(relx=1.0, rely=0.5, anchor="e", x=-4)

        # 发送按钮
        self.send_button = ctk.CTkButton(
            frame, 
            text="发送", 
            width=70,
            height=36,
            corner_radius=8,
            command=self._submit_task,
            font=ctk.CTkFont(weight="bold")
        )
        self.send_button.grid(row=0, column=1, pady=(0, 10), sticky="ew")

        # 状态栏文字
        self.status_label = ctk.CTkLabel(
            frame,
            textvariable=self.status_var,
            anchor="w",
            text_color=("gray30", "gray70"),
            font=ctk.CTkFont(size=12)
        )
        self.status_label.grid(row=1, column=0, sticky="w")

        # 模型下拉框
        self.model_dropdown = ctk.CTkOptionMenu(
            frame,
            values=["gpt-5.4-mini", "somnet-4-6", "Gemini-3"],
            variable=self.model_var,
            width=110,      
            height=24,     
            corner_radius=6,
            font=ctk.CTkFont(size=11),
            fg_color=("gray85", "gray20"),
            button_color=("gray80", "gray15"),
            button_hover_color=("gray75", "gray10"),
            text_color=("black", "white"),
            dynamic_resizing=False 
        )
        self.model_dropdown.grid(row=1, column=1, sticky="e")

        frame.columnconfigure(0, weight=1)

    def _place_window(self) -> None:
        width = 365
        height = 120
        screen_width = self.root.winfo_screenwidth()
        x = max(screen_width - width - 24, 24)
        y = 24
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    # ===== 语音输入相关逻辑 =====
    def _on_mic_click(self) -> None:
        if self._running:
            return
            
        self._running = True
        self.status_var.set("🎙️ 正在聆听...")
        
        # 禁用组件，防止录音时误触
        self.send_button.configure(state="disabled")
        self.input_entry.configure(state="disabled")
        self.mic_button.configure(state="disabled")
        self.model_dropdown.configure(state="disabled")

        # 开启子线程模拟语音识别过程，防止阻塞主UI
        threading.Thread(target=self._mock_voice_recognition, daemon=True).start()

    def _mock_voice_recognition(self) -> None:
        # 这里是模拟录音和调取 ASR (语音识别) 接口的时间
        time.sleep(1.5) 
        
        recognized_text = "帮我拉一个下午三点的讨论会" 
        
        # 使用 root.after 确保在主线程更新 UI (Tkinter 强制要求)
        self.root.after(0, self._finish_voice_input, recognized_text)

    def _finish_voice_input(self, text: str) -> None:
        self._running = False
        self.status_var.set("🟢 空闲")
        
        # 恢复组件状态
        self.send_button.configure(state="normal")
        self.input_entry.configure(state="normal")
        self.mic_button.configure(state="normal")
        self.model_dropdown.configure(state="normal")
        
        # 将识别到的文字填入输入框
        self.input_entry.delete(0, 'end')
        self.input_entry.insert(0, text)
        self.input_entry.focus_set() # 焦点给到输入框，方便用户按回车直接发送

    # ===== 常规指令发送逻辑 =====
    def _on_submit(self, _event: tk.Event) -> None:
        self._submit_task()

    def _submit_task(self) -> None:
        if self._running:
            return

        task = self.input_entry.get().strip()
        if not task:
            self.status_var.set("⚠️ 失败：任务为空")
            return

        self._running = True
        self.status_var.set("⏳ 操作执行中...")
        
        self.send_button.configure(state="disabled")
        self.input_entry.configure(state="disabled")
        self.mic_button.configure(state="disabled") # 执行任务时也禁用麦克风
        self.model_dropdown.configure(state="disabled")

        worker = threading.Thread(
            target=self._run_task,
            args=(task,),
            daemon=True,
        )
        worker.start()

    def _run_task(self, task: str) -> None:
        try:
            from agent.vision_loop import VisionDecisionLoop
            
            result = VisionDecisionLoop().run(task)
            if result.handoff_required:
                message = f"🟠 需要接管：{result.handoff_reason or '请处理当前界面'}"
                self._result_queue.put(("handoff", message))
            elif result.success:
                self._result_queue.put(("success", "✅ 完成"))
            else:
                self._result_queue.put(("failure", f"❌ 失败：{result.error or '任务未完成'}"))
        except Exception as exc:
            self._result_queue.put(("failure", f"❌ 失败：{exc}"))

    def _poll_result_queue(self) -> None:
        try:
            while True:
                _kind, message = self._result_queue.get_nowait()
                self.status_var.set(message)
                self._running = False
                
                self.send_button.configure(state="normal")
                self.input_entry.configure(state="normal")
                self.mic_button.configure(state="normal") # 恢复麦克风
                self.model_dropdown.configure(state="normal")
                
                self.input_entry.delete(0, 'end')
        except queue.Empty:
            pass
        finally:
            self.root.after(120, self._poll_result_queue)

    def run(self) -> None:
        self.root.mainloop()

def main() -> None:
    QuickCommandWindow().run()

if __name__ == "__main__":
    main()