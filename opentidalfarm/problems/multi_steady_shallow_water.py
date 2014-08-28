from problem import Problem
from steady_shallow_water import SteadyShallowWaterProblemParameters
from steady_shallow_water import SteadyShallowWaterProblem
from dolfin_adjoint import Constant


class MultiSteadyShallowWaterProblemParameters(SteadyShallowWaterProblemParameters):
    """ A set of parameters for a :class:`MultiSteadyShallowWaterProblem`.

    The parameters are described as in
    :class:`SteadyShallowWaterProblemParameters`.

    In addition following parameters are available:

    Time parameters:

    :ivar dt: The timestep. Default: 1.0.
    :ivar start_time: The start time. Default: 0.0.
    :ivar finish_time: The finish time. Default: 100.0.
    """

    # Time parameters
    dt = 1.
    start_time = 0.0
    finish_time = 100.0

    # Functional time integration parameters
    functional_final_time_only = False

class MultiSteadyShallowWaterProblem(SteadyShallowWaterProblem):

    def __init__(self, parameters):
        """ Instantiates a new :class:`ShallowWaterProblem` object. 

            :parameter parameters: A :class:`ShallowWaterProblemParameters`
                object containing the parameters of the problem.
        """

        if not type(parameters) == MultiSteadyShallowWaterProblemParameters:
            raise TypeError("parameters must be of type \
MultiSteadyShallowWaterProblemParameters.")

        if float(parameters.start_time) >= float(parameters.finish_time):
            raise ValueError("start_time must be < finish_time.")

        super(MultiSteadyShallowWaterProblem, \
                self).__init_without_type_check__(parameters)

    @property
    def _is_transient(self):
        return True

    @staticmethod
    def default_parameters():
        ''' Returns a dictionary with the default parameters '''

        return MultiSteadyShallowWaterProblemParameters()