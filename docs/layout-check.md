# Installed iPhone layout check

This temporary diagnostic uses the live dashboard and menu. It measures their layout inside the installed app, where the reported bottom gap occurs. Desktop emulation cannot establish where iOS draws the Home indicator, so the screenshots are part of the test.

1. Pull the current `develop` image and restart Command Deck.
2. Open Command Deck from the iPhone Home Screen. Tap the date and time, then **Layout check**.
3. Wait for **Fresh server CSS** to finish checking. Take a full-screen screenshot without cropping the Home indicator.
4. Tap **Scroll to end**. Take another full-screen screenshot. Tap **Copy report** and paste the text with both screenshots.
5. Repeat in Safari if the installed app shows the gap there too. Include whether each screenshot is from the installed app or Safari.

The cyan outline is the menu box. Amber marks the menu's bottom padding. The pink line marks the bottom of the CSS viewport independently of the menu. Compare that line with the physical Home indicator in the screenshot. **Assets: CHECK** means the loaded HTML, loaded stylesheet, and freshly fetched server stylesheet disagree; update or caching must be resolved before interpreting the geometry. **Fixed at CSS bottom**, **padding formula**, and **link gap** check the intended menu geometry. **Menu steady** checks whether it stays in place after scrolling. A pass for these CSS checks does not prove alignment with the physical screen edge; the screenshot establishes that.

The report contains viewport and layout measurements, a timestamp, path, and browser user agent. It does not include monitoring data or credentials. Close the diagnostic with **Close** in its panel.
