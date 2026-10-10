import autoscripts

autoscripts.configure(paths=".")
print("Discovered:", autoscripts.available())
from autoscripts import hello
print(hello.greet("Python"))
