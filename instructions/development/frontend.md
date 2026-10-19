# Guidelines for React TypeScript projects

## Architecture

- When creating new projects, use Next.js
- If a React component file is longer than 150 lines, check if it can be refactored into smaller components. Oftentimes, the JSX can be broken into smaller individual ui components, and the state logic can be extracted into custom hooks.
- When asked to combine two components into a shared component, avoid If/else statements inside the shared component by passing all unique values as props or boolean flags.
- In a React project, if you have a choice to use HTML state management, do not use it. Always default to standard React state management instead with controlled inputs.

## Component Extraction and Reusability

- **Extract shared components proactively**: When I see similar components (like RoomForm and FurnitureForm), I should suggest creating a shared base component to eliminate duplication.
- **Prefer flexible props over computed strings**: Instead of creating props like `title` that contain computed values (e.g., `initialValues ? 'Edit Room' : 'Add Room'`), use simpler, more flexible props like `label: 'Room'` and compute the final string inside the component (`{initialValues ? 'Edit' : 'Add'} {label}`).
- **Keep prop interfaces minimal and composable**: Use basic values that can be combined rather than pre-computed complex strings. This makes components more reusable and easier to maintain.
- **Centralize defaults and constants**: Use objects like `DEFAULTS_BY_LABEL` to manage default values based on the entity/label/type rather than hardcoding values.
- **Extract utility functions**: When components have complex default value generation or calculations, extract these into pure utility functions at the module level. Group related functions together (e.g., `calculateFeet`, `calculateInches`, `generateX`, `generateY`, `generateName`) and create higher-level functions that compose them (e.g., `generateDefaultValues`, `calculateFeetAndInches`). This improves readability and makes the component logic clearer.
- **Use custom hooks directly to avoid prop drilling**: Call custom hooks directly in components that need their functionality rather than passing functions through props. Only extract specific functions in parent components for global concerns like keyboard shortcuts. This reduces prop interface complexity and makes components more self-contained.

## Testing

- Run 'npm run build' when looking to run a typecheck.
- Write unit tests for all utility functions that are defined in their own files.
- Use React Testing Library for component tests and custom hooks.

## Deployment / Uptime Monitoring

- **Not for my personal (`jnelken`-owned) repos** — none of them are P0 for uptime, so don't raise monitoring for them at all, on a push to main or otherwise. Check `git remote get-url origin` if unsure. Only suggest it if I ask.
- For everything else: whenever active work is being pushed to main (i.e. deploying to production), consider whether the project has uptime monitoring set up, and suggest it if not.
- Default to UptimeRobot's free tier (uptimerobot.com) — free monitors with a public status page, no added cost. Only reach for a paid/Datadog-style synthetic monitoring setup if the user explicitly asks for deeper APM correlation.
