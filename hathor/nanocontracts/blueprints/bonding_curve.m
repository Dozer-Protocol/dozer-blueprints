% Define the exponential curve function
exp_curve = @(x, a, b, c) a * (exp(b*x) - 1) + c;

% Set up the x-axis (token supply)
x = linspace(0, 8e6, 1000);

% Initial parameters (these will need tuning)
a = 0.005;  % Amplitude
b = 5.3335e-7;   % Growth rate
c = 0.01;   % Vertical shift (initial price)

% Calculate prices
prices = exp_curve(x, a, b, c);

% Plot the curve
plot(x, prices);
xlabel('Token Supply');
ylabel('Price (HTR)');
title('Exponential Bonding Curve');
grid on;

% Check initial price
fprintf('Initial price: %.4f HTR\n', prices(1));

% Calculate total HTR collected (area under the curve)
total_htr = trapz(x, prices);
fprintf('Total HTR collected by launchpad: %.2f HTR\n', total_htr);

% Display final price
fprintf('Final price at 800M tokens: %.4f HTR\n', prices(end));
fprintf('Pump %.4f times\n', prices(end)/c);

% Calculate price at key points
price_at_25percent = exp_curve(2e6, a, b, c);
price_at_50percent = exp_curve(4e6, a, b, c);
price_at_75percent = exp_curve(6e6, a, b, c);


fprintf('Price at 25%% (2M tokens): %.4f HTR\n', price_at_25percent);
fprintf('Price at 50%% (4M tokens): %.4f HTR\n', price_at_50percent);
fprintf('Price at 75%% (6M tokens): %.4f HTR\n', price_at_75percent);

% Calculate HTR collected at key points
htr_at_25percent = trapz(x(1:250), prices(1:250));
htr_at_50percent = trapz(x(1:500), prices(1:500));
htr_at_75percent = trapz(x(1:750), prices(1:750));
htr_at_100percent = trapz(x(1:1000), prices(1:1000));


fprintf('HTR collected at 25%%: %.2f HTR\n', htr_at_25percent);
fprintf('HTR collected at 50%%: %.2f HTR\n', htr_at_50percent);
fprintf('HTR collected at 75%%: %.2f HTR\n', htr_at_75percent);
fprintf('HTR collected at 100%%: %.2f HTR\n', htr_at_100percent);