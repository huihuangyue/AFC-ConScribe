// 前端逻辑：将任务发送给后端 /api/plan_task，并在聊天框展示结果；以及调用 /api/execute_task 执行技能。

// 最近一次规划得到的技能信息（供“执行技能”按钮复用）
let LAST_PLAN = null;

function appendMessage (role, text) {
  const chatHistory = document.getElementById("chatHistory");
  if (!chatHistory) return;

  const msg = document.createElement("div");
  msg.classList.add("message");
  msg.classList.add(role === "user" ? "user-msg" : "agent-msg");

  const avatar = document.createElement("div");
  avatar.classList.add("avatar");
  avatar.textContent = role === "user" ? "U" : "A";

  const bubble = document.createElement("div");
  bubble.classList.add("bubble");
  bubble.textContent = text;

  msg.appendChild(avatar);
  msg.appendChild(bubble);
  chatHistory.appendChild(msg);

  // 滚动到底部
  chatHistory.scrollTop = chatHistory.scrollHeight;
}

async function sendTask (alsoExecute = false) {
  const input = document.getElementById("userInput");
  if (!input) return;
  const text = (input.value || "").trim();
  if (!text) {
    return;
  }

  // 1) 把用户输入追加到聊天记录中
  appendMessage("user", text);

  // 2) 清空输入框
  input.value = "";

  // 3) 追加一个占位的“正在规划任务…”提示
  appendMessage("agent", "正在根据任务规划技能并生成调用代码，请稍候…");

  // 4) 调用后端 API
  const payload = {
    run_dir: window.CURRENT_RUN_DIR || "",
    task: text,
  };

  try {
    const resp = await fetch("/api/plan_task", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await resp.json();

    if (!data.ok) {
      const errMsg = data.error || "未知错误";
      appendMessage(
        "agent",
        "规划失败：\n" +
        errMsg +
        "\n\n请检查 run_dir 是否正确、后端日志是否有报错。"
      );
      return;
    }

    const skillId = data.skill_id || "(未知)";
    const skillPath = data.skill_path || "(未知路径)";
    const callStr = data.call_str || "(未生成调用代码)";

    // 记录本次规划结果，供后续“执行技能”按钮使用
    LAST_PLAN = {
      run_dir: window.CURRENT_RUN_DIR || "",
      task: text,
      skill_id: skillId,
      skill_path: skillPath,
      call_str: callStr,
    };

    const summaryLines = [];
    summaryLines.push("✅ 任务规划完成，已选定技能：");
    summaryLines.push("");
    summaryLines.push("  • 技能 ID ：" + skillId);
    summaryLines.push("  • 技能文件：" + skillPath);
    summaryLines.push("");
    summaryLines.push("建议调用代码：");
    summaryLines.push("");
    summaryLines.push(callStr);

    appendMessage("agent", summaryLines.join("\n"));

    // 若需要，规划完成后自动执行技能
    if (alsoExecute) {
      await executeSkill(true);
    }
  } catch (e) {
    appendMessage(
      "agent",
      "调用后端接口失败：\n" +
      (e && e.message ? e.message : String(e)) +
      "\n\n请确认 front/run.py 是否正在运行。"
    );
  }
}

async function executeSkill (autoFromPlan = false) {
  if (!LAST_PLAN || !LAST_PLAN.skill_path || !LAST_PLAN.call_str) {
    appendMessage(
      "agent",
      "当前没有可执行的技能结果，请先在右侧输入任务并完成一次规划。"
    );
    return;
  }

  if (autoFromPlan) {
    appendMessage(
      "agent",
      "已根据当前任务选定技能，开始执行，请在左侧 VNC 窗口中观察浏览器动作…"
    );
  } else {
    appendMessage(
      "agent",
      "开始执行上一条规划得到的技能，请在左侧 VNC 窗口中观察浏览器动作…"
    );
  }

  const payload = {
    run_dir: window.CURRENT_RUN_DIR || "",
    skill_path: LAST_PLAN.skill_path,
    call_str: LAST_PLAN.call_str,
    slow_mo_ms: 200,
    default_timeout_ms: 15000,
    keep_open: true,
  };

  try {
    const resp = await fetch("/api/execute_task", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await resp.json();
    if (!data.ok) {
      appendMessage(
        "agent",
        "技能执行失败：\n" +
        (data.error || "unknown_error") +
        "\n\n详情：" +
        JSON.stringify(data, null, 2)
      );
      return;
    }

    appendMessage(
      "agent",
      "✅ 技能执行完成。\n\n" +
      "技能 ID: " +
      (data.skill_id || "(未知)") +
      "\n" +
      "技能文件: " +
      (data.skill_path || "(未知路径)") +
      "\n" +
      "调用代码:\n" +
      (data.call_str || "")
    );
  } catch (e) {
    appendMessage(
      "agent",
      "执行技能时调用后端失败：\n" +
      (e && e.message ? e.message : String(e)) +
      "\n\n请确认 front/run.py 是否正在运行。"
    );
  }
}

// 支持在 textarea 回车发送（Shift+Enter 换行）
window.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("userInput");
  if (!input) return;
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submitUnified();
    }
  });
});

// 对外暴露的统一入口：一次性完成“规划 + 执行”或 “browser-use 执行”（由 EXEC_MODE 决定）
async function submitUnified () {
  const mode = (window.EXEC_MODE || "planner").toLowerCase();
  if (mode === "browser_use") {
    await submitWithBrowserUse();
  } else {
    await submitAndExecute();
  }
}

// 旧的规划 + 技能执行入口，保留以便切换 EXEC_MODE 时复用
async function submitAndExecute () {
  await sendTask(true);
}

// 使用 browser-use Agent（复用 VNC Chrome）直接执行任务
async function submitWithBrowserUse () {
  const input = document.getElementById("userInput");
  if (!input) return;
  const text = (input.value || "").trim();
  if (!text) {
    return;
  }

  // 1) 聊天面板显示用户输入
  appendMessage("user", text);
  input.value = "";

  // 2) 提示正在通过 browser-use 执行
  appendMessage(
    "agent",
    "正在通过浏览器智能体执行任务，请在左侧 VNC 窗口中观察浏览器操作…"
  );

  const payload = {
    task: text,
    max_steps: 10,
    // start_url 由后端默认使用当前 BROWSER_URL，除非以后需要显式覆盖
  };

  try {
    const resp = await fetch("/api/browser_use_run", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const data = await resp.json();
    // 调试：仅在浏览器控制台打印原始 JSON，避免在对话框里展示冗长结构化结果
    try {
      console.log("[browser_use_front] /api/browser_use_run result:", data);
      // 如需在聊天窗中查看原始 JSON，可临时取消下一行注释：
      // appendMessage("agent", "【browser-use 原始结果】\n" + JSON.stringify(data, null, 2));
    } catch (e) {
      // 即使这里出错也不要影响后续正常分支
    }
    const lines = [];

    // 统一在右侧对话框中给出“任务结果 / 失败原因”的自然语言反馈
    if (!data.ok) {
      lines.push("❌ 执行失败。");
      if (data.task) {
        lines.push("");
        lines.push("任务：");
        lines.push(String(data.task));
      }
      if (data.error) {
        lines.push("");
        lines.push("错误信息：");
        lines.push(String(data.error));
      } else {
        lines.push("");
        lines.push("错误信息：未知错误（请查看后端日志 browser_service ）。");
      }

      // 若有部分步骤已执行，附上已完成的操作，方便用户理解“做到哪一步失败”
      if (Array.isArray(data.steps) && data.steps.length > 0) {
        lines.push("");
        lines.push("在失败之前已尝试的操作步骤：");
        data.steps.forEach((s, idx) => {
          lines.push(String(idx + 1) + ". " + String(s));
        });
      }

      appendMessage("agent", lines.join("\n"));
      return;
    }

    // 成功场景：尽量给出“答案”而不是只说完成
    lines.push("✅ browser 执行完成。");
    if (data.task) {
      lines.push("");
      lines.push("任务：");
      lines.push(String(data.task));
    }

    // 任务回答：优先使用后端返回的 final_result；若为空则给一个友好的解释
    let answer = (data.final_result || "").trim();
    if (!answer) {
      if (Array.isArray(data.steps) && data.steps.length > 0) {
        answer =
          "已在浏览器中完成相关操作，但模型未给出结构化文字回答。" +
          "\n请结合左侧浏览器当前页面内容查看结果，以下为主要操作步骤概览。";
      } else {
        answer =
          "已在浏览器中完成相关操作，但未能从页面中提取明确答案，可能需要人工查看左侧浏览器中的页面内容。";
      }
    }

    lines.push("");
    lines.push("回答：");
    lines.push(answer);

    // 若后端提供了逐步操作描述，则一并展示到对话记录中，方便用户理解执行过程
    if (Array.isArray(data.steps) && data.steps.length > 0) {
      lines.push("");
      lines.push("操作步骤：");
      data.steps.forEach((s, idx) => {
        lines.push(String(idx + 1) + ". " + String(s));
      });
    }

    appendMessage("agent", lines.join("\n"));
  } catch (e) {
    appendMessage(
      "agent",
      "调用 /api/browser_use_run 失败：\n" +
      (e && e.message ? e.message : String(e)) +
      "\n\n请确认 front/run.py 正在运行且后端环境已安装 browser。"
    );
  }
}
