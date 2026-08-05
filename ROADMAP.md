# Roadmap

## Completed
- ✅ Renamed repo from `claude-skills` to `claude-tools`
- ✅ Created `/claude-tools` gallery page on portfolio with parallax scrolling
  - 5 featured tools with interactive showcase cards
  - 7 additional tools in clean list format
  - Designed with gradients inspired by bg-generator aesthetic
  - Live at `localhost:3000/claude-tools`

## In Progress
- Portfolio landing page liquid-metal cursor-following effect
  - Enhance existing `useClipPathEffect` with smooth morphing accent
  - Connect to same mouse-tracking infrastructure
  - Cycle colors with existing click handler
  - Link: https://app.paper.design/playground/liquid-metal

## Design System & Visual Effects

### Background Effects
- **Shader Gradient** (https://shadergradient.co/customize) for animated backgrounds
  - Preferred for hero sections, featured showcase areas, dividers
  - Export as WebP or MP4 for performance
  - Example preset: warm, city-lit, animated
  - Keep colors aligned with portfolio palette (warm browns, creams, accent oranges)

### Interactive Elements
- Cursor-following liquid metal for premium feel
- Parallax scrolling on content-heavy pages
- Clip-path morphing based on mouse position

## Future Enhancements
- [ ] Add liquid-metal background to additional portfolio pages
- [ ] Integrate Shader Gradient animations into project showcase sections
- [ ] Expand claude-tools gallery with more skills as they're added
- [ ] Create interactive demos/playgrounds for featured tools
- [ ] Add video tutorials or GIF walkthroughs for complex skills
- [ ] Build searchable/filterable tools interface (by category, use case)
- [ ] Create embedded skill cards for README documentation

## Portfolio Integration Ideas
- Landing page: liquid-metal accent with cursor tracking ✨
- Projects page: shader-gradient animated hero
- Claude Tools page: parallax showcase (done!)
- Statusline page: interactive preview with dynamic colors
- About/contact: flowing gradient background

## Technical Debt
- None identified yet

## Notes
- All new visual effects should maintain consistency with existing design system (Suisse Intl font, dark warm brown bg, cream foreground, subtle shadows)
- Prefer interactive effects that respond to user input (mouse, scroll) over purely animated backgrounds
- Performance: Use WebP/MP4 exports for gradients; keep canvas-based effects optimized for 60fps
