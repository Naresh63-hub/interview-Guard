// background.js - Chrome Extension Service Worker

chrome.runtime.onInstalled.addListener(() => {
  console.log("[InterviewOS Guard] Extension installed and active.");
});

// Monitor tab switches inside the browser
chrome.tabs.onActivated.addListener((activeInfo) => {
  chrome.tabs.get(activeInfo.tabId, (tab) => {
    if (tab && tab.url) {
      console.log(`[InterviewOS Guard] Tab switched. Current URL: ${tab.url}`);
    }
  });
});

// Monitor when browser windows are focused or unfocused
chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) {
    console.warn("[InterviewOS Guard] Browser lost system focus.");
  } else {
    console.log(`[InterviewOS Guard] Focused window ID: ${windowId}`);
  }
});
