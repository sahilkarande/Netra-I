// ========================================
// 🌓 THEME TOGGLE FUNCTIONALITY
// Light & Dark Mode Switcher
// ========================================

(function() {
  'use strict';

  // Get elements
  const themeToggle = document.getElementById('themeToggle');
  const htmlElement = document.documentElement;
  
  // Check for saved theme preference or default to 'light'
  const savedTheme = localStorage.getItem('theme');
  const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const currentTheme = savedTheme || (systemPrefersDark ? 'dark' : 'light');
  
  // Apply saved theme on page load
  htmlElement.setAttribute('data-theme', currentTheme);
  
  // Theme toggle function
  function toggleTheme() {
    const currentTheme = htmlElement.getAttribute('data-theme');
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    
    // Add transition for smooth theme change
    document.body.style.transition = 'background-color 0.3s ease, color 0.3s ease';
    
    // Set new theme
    htmlElement.setAttribute('data-theme', newTheme);
    localStorage.setItem('theme', newTheme);
    
    // Add rotation animation to button
    if (themeToggle) {
      themeToggle.style.transition = 'transform 0.3s ease';
      themeToggle.style.transform = 'rotate(360deg)';
      
      setTimeout(() => {
        themeToggle.style.transform = 'rotate(0deg)';
      }, 300);
    }
    
    // Log theme change (optional)
    console.log(`🌓 Theme switched to: ${newTheme}`);
    
    // Remove transition after change
    setTimeout(() => {
      document.body.style.transition = '';
    }, 300);
  }
  
  // Event listener for toggle button
  if (themeToggle) {
    themeToggle.addEventListener('click', function(e) {
      e.preventDefault();
      toggleTheme();
    });
  }
  
  // Optional: Listen for system theme changes
  const darkModeMediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
  
  darkModeMediaQuery.addEventListener('change', (e) => {
    // Only auto-switch if user hasn't set a preference
    if (!localStorage.getItem('theme')) {
      const newTheme = e.matches ? 'dark' : 'light';
      htmlElement.setAttribute('data-theme', newTheme);
      console.log(`🌓 System theme changed to: ${newTheme}`);
    }
  });
  
  // Initialize on page load
  console.log(`🌓 Current theme: ${currentTheme}`);
  
  // Keyboard shortcut: Ctrl/Cmd + Shift + D to toggle theme
  document.addEventListener('keydown', function(e) {
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'D') {
      e.preventDefault();
      toggleTheme();
    }
  });
  
})();